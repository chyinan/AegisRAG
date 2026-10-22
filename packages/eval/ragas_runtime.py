"""Small, text-only compatibility boundary for the locked Ragas 0.4.3 API.

Ragas' original AnswerRelevancy performs synchronous embedding I/O inside its
async scorer. Prefetch those vectors asynchronously after question generation;
leave its question prompt, noncommittal handling and scoring formula untouched.
"""

from __future__ import annotations

import logging
from contextvars import Context
from typing import Any

from langchain_core.globals import get_debug
from langchain_core.tracers.context import _configure_hooks
from langchain_core.utils.env import env_var_is_set
from ragas._analytics import do_not_track
from ragas.embeddings.base import LangchainEmbeddingsWrapper
from ragas.integrations.helicone import helicone_config
from ragas.metrics._answer_relevance import AnswerRelevancy, ResponseRelevancePrompt
from ragas.metrics._context_precision import ContextPrecision
from ragas.metrics._context_recall import ContextRecall
from ragas.metrics._faithfulness import Faithfulness


class RagasConfigurationError(RuntimeError):
    """Evaluation cannot run with the current optional dependency/privacy settings."""


def assert_private_execution() -> None:
    """Fail closed without modifying environment, logging or framework globals."""
    if not do_not_track():
        raise RagasConfigurationError(
            "Set RAGAS_DO_NOT_TRACK=true before starting the evaluation process."
        )
    if helicone_config.is_enabled:
        raise RagasConfigurationError("Ragas Helicone tracing must be disabled.")
    # Some community callback hooks ignore LangSmith's disabled context.
    # Read the registry without changing it, and reject any hook that could be
    # recreated from environment or a ContextVar default in our fresh task.
    if any(env_var_is_set(name) for name in ("LANGCHAIN_WANDB_TRACING", "LANGCHAIN_COMET_TRACING")):
        raise RagasConfigurationError("Environment-enabled framework callbacks must be disabled.")
    empty_context = Context()
    for variable, _, handler_class, environment_name in tuple(_configure_hooks):
        if handler_class is not None and environment_name and env_var_is_set(environment_name):
            raise RagasConfigurationError(
                "Environment-enabled framework callbacks must be disabled."
            )
        try:
            default_handler = empty_context.run(variable.get)
        except LookupError:
            default_handler = None
        if default_handler is not None:
            raise RagasConfigurationError("Default framework callbacks must be disabled.")
    logger_names: tuple[str, ...] = (
        "openai._base_client",
        "openai._response",
        "ragas._analytics",
        "ragas.prompt",
        "ragas.llms",
        "ragas.embeddings",
    )
    # ragas.utils installs its own DEBUG handler at import; it is not on this
    # text scoring path. Guard the components that can handle payloads.
    logger_names += tuple(
        name
        for name in logging.Logger.manager.loggerDict
        if name.startswith(("ragas.prompt.", "ragas.llms.", "ragas.embeddings."))
    )
    if get_debug() or any(
        logging.getLogger(name).isEnabledFor(logging.DEBUG) for name in logger_names
    ):
        raise RagasConfigurationError("Framework and SDK debug logging must be disabled.")


class _PrefetchedEmbeddings(LangchainEmbeddingsWrapper):
    """Per-metric vectors only; synchronous scoring cannot initiate I/O."""

    def __init__(self, embeddings: Any, run_config: Any) -> None:
        super().__init__(embeddings, run_config=run_config)
        self._question: tuple[str, list[float]] | None = None
        self._documents: tuple[list[str], list[list[float]]] | None = None

    async def prepare(self, question: str, questions: list[str]) -> None:
        # Keep query/document roles separate even if an embedding model uses
        # distinct query and document representations.
        query_vector = await self.aembed_query(question)
        document_vectors = await self.aembed_documents(questions)
        self._question = (question, query_vector)
        self._documents = (questions, document_vectors)

    def embed_query(self, text: str) -> list[float]:
        if self._question is None or text != self._question[0]:
            raise RuntimeError("Ragas attempted an unprepared synchronous embedding.")
        return self._question[1]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._documents is None or texts != self._documents[0]:
            raise RuntimeError("Ragas attempted an unprepared synchronous embedding.")
        return self._documents[1]


class _PrefetchRelevancePrompt(ResponseRelevancePrompt):
    def __init__(self, question: str, embeddings: _PrefetchedEmbeddings) -> None:
        super().__init__()
        self._question = question
        self._embeddings = embeddings

    async def generate_multiple(self, *args: Any, **kwargs: Any) -> Any:
        responses = await super().generate_multiple(*args, **kwargs)
        questions = [response.question for response in responses]
        # Match the upstream all-empty branch, which returns NaN without I/O.
        if not all(question == "" for question in questions):
            await self._embeddings.prepare(self._question, questions)
        return responses


def create_metrics(
    names: tuple[str, ...], llm: Any, embeddings: Any, run_config: Any, question: str
) -> list[Any]:
    classes = {
        "context_precision": ContextPrecision,
        "context_recall": ContextRecall,
        "faithfulness": Faithfulness,
        "answer_relevancy": AnswerRelevancy,
    }
    metrics = []
    for name in names:
        if name == "answer_relevancy":
            prepared = _PrefetchedEmbeddings(embeddings, run_config)
            metric = AnswerRelevancy(llm=llm, embeddings=prepared)
            metric.question_generation = _PrefetchRelevancePrompt(question, prepared)
        else:
            metric = classes[name](llm=llm)
        metric.init(run_config)
        metrics.append(metric)
    return metrics
