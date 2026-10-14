#!/usr/bin/env python3
"""
RAG Evaluation Runner — scores authorized captured runs or live answer relevancy.

Usage (capture answers and exact generation contexts before running):
    RAGAS_DO_NOT_TRACK=true python evaluation/evaluate.py \
      --dataset evaluation/dataset/sample.json \
      --responses captured-responses.json \
      --output evaluation/reports/

The live API does not expose full contexts; omit --responses only with
--metrics answer_relevancy. Configure judge and embedding endpoints explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from math import isfinite
from pathlib import Path

import httpx

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from packages.eval.ragas_evaluator import EvalCase, RagasEvaluator  # noqa: E402


class RagApiEvaluationError(RuntimeError):
    """The authorized API did not provide a complete, verifiable evaluation input."""


def load_dataset(path: str) -> tuple[list[EvalCase], str, str]:
    """Load evaluation dataset from JSON file.

    Expected format:
    {
        "dataset_name": "...",
        "dataset_version": "v1",
        "cases": [
            {
                "case_id": "001",
                "question": "...",
                "reference_answer": "...",       // optional
                "reference_contexts": ["..."]    // optional
            }
        ]
    }
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    dataset_name = data.get("dataset_name", Path(path).stem)
    dataset_version = data.get("dataset_version", "v1")

    cases = []
    for item in data.get("cases", []):
        cases.append(
            EvalCase(
                case_id=item["case_id"],
                question=item["question"],
                reference_answer=item.get("reference_answer"),
                reference_contexts=tuple(item.get("reference_contexts", [])),
                metadata=item.get("metadata"),
            )
        )

    return cases, dataset_name, dataset_version


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Captured response JSON must not contain duplicate object keys.")
        result[key] = value
    return result


def load_responses(
    path: str,
    cases: Sequence[EvalCase],
) -> dict[str, tuple[str, list[str]]]:
    """Load captured answers and exact generation contexts keyed by dataset case_id.

    The capture must come from an authorized run of the same dataset. Reference
    answers/contexts and citation excerpts must not be substituted for run output.
    An explicit empty list records a real run with no retrieved context.
    """
    with open(path, encoding="utf-8") as file:
        payload = json.load(file, object_pairs_hook=_unique_json_object)
    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Captured responses require unique dataset case_id values.")
    if not isinstance(payload, dict) or set(payload) != set(case_ids):
        raise ValueError("Captured responses must match dataset case_id values exactly.")
    responses: dict[str, tuple[str, list[str]]] = {}
    for case_id, value in payload.items():
        if not isinstance(value, dict):
            raise ValueError("Each captured response must be an object.")
        answer = value.get("answer")
        contexts = value.get("retrieved_contexts")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Each captured response requires a nonblank answer.")
        if not isinstance(contexts, list) or any(
            not isinstance(context, str) or not context.strip() for context in contexts
        ):
            raise ValueError("retrieved_contexts must be a list of nonblank strings.")
        responses[case_id] = (answer, contexts)
    return responses


async def _post_data(
    client: httpx.AsyncClient,
    *,
    url: str,
    body: dict[str, object],
    headers: dict[str, str],
) -> dict[str, object]:
    """Read the public API envelope without exposing raw response text or credentials."""
    try:
        response = await client.post(url, json=body, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RagApiEvaluationError(
            f"RAG API request failed (HTTP {exc.response.status_code})."
        ) from None
    except httpx.RequestError:
        raise RagApiEvaluationError("RAG API request failed (transport error).") from None
    try:
        envelope = response.json()
    except ValueError:
        raise RagApiEvaluationError("RAG API returned invalid JSON.") from None
    if not isinstance(envelope, dict) or envelope.get("error") is not None:
        raise RagApiEvaluationError("RAG API returned an invalid or failed response envelope.")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise RagApiEvaluationError("RAG API response is missing its data object.")
    return data


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if not isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and greater than zero")
    return seconds


async def query_rag(
    api_url: str,
    question: str,
    api_key: str | None = None,
    *,
    timeout: float = 60.0,
    transport: httpx.AsyncBaseTransport | None = None,
    allow_missing_contexts: bool = False,
) -> tuple[str, list[str]]:
    """Read /query and fail closed when full packed contexts are unavailable.

    The public API currently exposes citation identities and context counts, not
    the text supplied to generation. /retrieve likewise omits content, while
    /sources/resolve only offers bounded excerpts without a completeness marker.
    Neither is a safe substitute for the actual packed evaluation contexts.
    Only answer-relevancy evaluation may explicitly opt into missing contexts.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    base_url = api_url.rstrip("/")

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        data = await _post_data(
            client,
            url=f"{base_url}/query",
            body={"query": question, "top_k": 10},
            headers=headers,
        )
        answer = data.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise RagApiEvaluationError("RAG query response is missing a nonblank answer.")
        if allow_missing_contexts:
            return answer, []
        # Do not synthesize contexts from references, make a separate retrieval,
        # or read the database outside the caller's authorization boundary.
        raise RagApiEvaluationError(
            "Full packed contexts are unavailable from the current /query API. "
            "Citations and /sources/resolve excerpts cannot verify the exact "
            "generation context; evaluation requires an authorized full-context "
            "capture. No empty or approximate contexts were scored."
        )


async def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation against AegisRAG")
    parser.add_argument("--api-url", default="http://localhost:8000", help="AegisRAG API base URL")
    parser.add_argument(
        "--api-key", default=os.getenv("RAGAS_API_KEY"), help="Bearer token for the RAG API"
    )
    parser.add_argument("--api-timeout", type=_positive_seconds, default=60.0)
    parser.add_argument(
        "--judge-timeout",
        type=_positive_seconds,
        default=os.getenv("RAGAS_TIMEOUT_SECONDS", "60"),
        help="Timeout in seconds for each judge metric call",
    )
    parser.add_argument("--dataset", required=True, help="Path to evaluation dataset JSON")
    parser.add_argument(
        "--responses",
        help="Captured run JSON keyed by case_id, with answer and exact retrieved_contexts",
    )
    parser.add_argument(
        "--output", default="evaluation/reports", help="Output directory for reports"
    )
    parser.add_argument("--llm-model", default=os.getenv("RAGAS_LLM_MODEL", "gpt-4o-mini"))
    parser.add_argument("--llm-base-url", default=os.getenv("RAGAS_LLM_BASE_URL"))
    parser.add_argument("--llm-api-key", default=os.getenv("RAGAS_LLM_API_KEY"))
    parser.add_argument(
        "--embedding-model", default=os.getenv("RAGAS_EMBEDDING_MODEL", "text-embedding-3-small")
    )
    parser.add_argument("--embedding-base-url", default=os.getenv("RAGAS_EMBEDDING_BASE_URL"))
    parser.add_argument("--embedding-api-key", default=os.getenv("RAGAS_EMBEDDING_API_KEY"))
    parser.add_argument("--pass-threshold", type=float, default=0.70)
    parser.add_argument(
        "--metrics", default="context_precision,context_recall,faithfulness,answer_relevancy"
    )
    args = parser.parse_args(argv)

    metrics = tuple(metric.strip() for metric in args.metrics.split(","))
    context_free = metrics == ("answer_relevancy",)
    if args.responses is None and not context_free:
        parser.error(
            "The live /query API does not expose full packed contexts. Supply --responses "
            "with authorized captured answers and exact retrieved_contexts, or select "
            "--metrics answer_relevancy for answer-only live evaluation."
        )

    # Load dataset
    cases, dataset_name, dataset_version = load_dataset(args.dataset)
    if not cases:
        parser.error("The evaluation dataset must contain at least one case.")
    if set(metrics) & {"context_precision", "context_recall"} and any(
        not isinstance(case.reference_answer, str) or not case.reference_answer.strip()
        for case in cases
    ):
        parser.error(
            "context_precision/context_recall require a nonblank reference_answer for every "
            "case. reference_contexts are not a substitute."
        )
    print(f"Loaded {len(cases)} cases from {args.dataset}")

    responses = None
    if args.responses is not None:
        try:
            responses = load_responses(args.responses, cases)
        except (OSError, ValueError) as exc:
            parser.error(f"Invalid captured response file: {exc}")

    # Create evaluator
    evaluator = RagasEvaluator(
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        llm_api_key=args.llm_api_key,
        embedding_model=args.embedding_model,
        embedding_base_url=args.embedding_base_url,
        embedding_api_key=args.embedding_api_key,
        timeout_seconds=args.judge_timeout,
        generation_timeout_seconds=args.api_timeout,
        pass_threshold=args.pass_threshold,
        metrics=metrics,
    )

    # Run evaluation
    if responses is None:
        print(f"Evaluating answer relevancy against {args.api_url} ...")
    else:
        print("Evaluating captured answers and contexts ...")
    ordered_cases = iter(cases)

    async def run_case(question: str) -> tuple[str, list[str]]:
        if responses is not None:
            # RagasEvaluator invokes run_fn once per case in dataset order. Use
            # case IDs rather than a question dictionary: questions may repeat.
            case = next(ordered_cases, None)
            if case is None or case.question != question:
                raise ValueError("Captured response order does not match the dataset.")
            return responses[case.case_id]
        return await query_rag(
            args.api_url,
            question,
            args.api_key,
            timeout=args.api_timeout,
            allow_missing_contexts=True,
        )

    report = await evaluator.evaluate(
        cases=cases,
        run_fn=run_case,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
    )

    # Save reports
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON report (compatible with EvalEvidenceService)
    json_path = output_dir / f"eval_{report.run_id}.json"
    evidence_report = evaluator.to_evidence_report(report)
    json_path.write_text(
        json.dumps(evidence_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"JSON report: {json_path}")

    # Markdown report
    md_path = output_dir / f"eval_{report.run_id}.md"
    md_path.write_text(evaluator.to_markdown(report), encoding="utf-8")
    print(f"Markdown report: {md_path}")

    # Summary
    print()
    print("=" * 50)
    print(f"Passed: {report.passed_count}/{report.case_count}")
    for name, score in sorted(report.aggregate_scores.items()):
        emoji = "✅" if score >= args.pass_threshold else "❌"
        print(f"  {name}: {emoji} {score:.4f}")
    print("=" * 50)

    if report.failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
