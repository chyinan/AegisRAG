"""Graph RAG — knowledge-graph-augmented retrieval pipeline.

The Graph RAG module complements traditional dense retrieval by
building a knowledge graph from ingested documents and using
graph-traversal to answer relationship-oriented and summary-type
questions that vector similarity alone struggles with.

Architecture:
  1. Build phase: LLM extracts (entity, relation, entity) triples from
     each document chunk. Triples are deduplicated and merged into a
     NetworkX directed multigraph.
  2. Query phase:
     a. Extract seed entities from the user query.
     b. Traverse the neighbourhood of those entities (configurable hops).
     c. Collect related chunks (the "graph context").
     d. Return graph-context results alongside dense retrieval for
        the final generation step.

The module accepts a simple async callable (system_prompt, user_prompt) -> str
so callers wire up their own LLM provider (e.g. packages.llm.ports.LLMProvider).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import networkx as nx

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# An async function that takes (system_prompt, user_prompt) and returns text.
LLMCallback = Callable[[str, str], Awaitable[str]]

# ---------------------------------------------------------------------------
# LLM prompts (tuned for DeepSeek / Hermes OpenAI-compatible endpoints)
# ---------------------------------------------------------------------------

_EXTRACT_TRIPLES_SYSTEM = """\
You are a knowledge-graph extraction engine. Given a text passage, \
extract all (subject, relation, object) triples where:
- subject and object are named entities: people, organisations, \
  technologies, frameworks, concepts, metrics, dates, locations.
- relation is a concise verb phrase (max 8 words) that captures the \
  semantic link between subject and object.

Return ONLY a JSON array of objects with keys "subject", "relation", \
"object". No markdown, no explanation.

Example input: "PostgreSQL supports HNSW indexing via pgvector."
Example output:
[{"subject": "PostgreSQL", "relation": "supports indexing algorithm", "object": "HNSW"},
 {"subject": "pgvector", "relation": "provides", "object": "HNSW indexing"}]
"""

_QUERY_ENTITIES_SYSTEM = """\
Extract the key entities from this question. Return ONLY a JSON array \
of strings. Include named entities, technical terms, and domain concepts.

Example input: "How does pgvector compare to Milvus for vector search?"
Example output: ["pgvector", "Milvus", "vector search"]
"""

_GRAPH_SUMMARY_SYSTEM = """\
You are a knowledge-graph summariser. Given a set of triplets from a \
knowledge graph around a user's question, produce a concise summary \
(at most 3 paragraphs) that captures the key relationships and insights \
relevant to the question. Focus on connections that answer "why" and \
"how", not just "what"."""


class GraphRAGPipeline:
    """End-to-end Graph RAG: build graph from chunks, query graph at runtime.

    Accepts an async LLM callback so it stays decoupled from the project's
    LLM provider infrastructure. Typical wiring:

        async def llm_cb(system: str, user: str) -> str:
            req = GenerateRequest(
                messages=(LLMMessage(role="system", content=system),
                          LLMMessage(role="user", content=user)),
                provider=settings.llm_provider,
                model="deepseek-v4-flash",
                ...
            )
            resp = await llm_provider.generate(req)
            return resp.text

        graph_rag = GraphRAGPipeline(llm=llm_cb)
    """

    def __init__(
        self,
        llm: LLMCallback,
        *,
        max_neighbour_hops: int = 2,
        max_triples_per_chunk: int = 8,
    ) -> None:
        self._llm = llm
        self._max_hops = max_neighbour_hops
        self._max_triples = max_triples_per_chunk

    # ------------------------------------------------------------------
    # Build phase
    # ------------------------------------------------------------------

    async def build_graph(
        self,
        *,
        tenant_id: str,
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Extract triples from chunks and merge into the knowledge graph.

        Each chunk dict should have at least: chunk_id, text.
        Returns build stats: {triple_count, chunk_count, triples}.
        """
        all_triples: list[dict[str, str]] = []
        for chunk in chunks:
            text = chunk.get("text", "")
            if not text or len(text.strip()) < 20:
                continue
            triples = await self._extract_triples(text[:3000])
            for t in triples:
                t["_chunk_id"] = str(chunk.get("chunk_id", ""))
            all_triples.extend(triples)

        return {
            "triple_count": len(all_triples),
            "chunk_count": len(chunks),
            "triples": all_triples,
        }

    async def build_graph_inline(
        self,
        tenant_id: str,
        chunks: list[dict[str, Any]],
        graph: nx.DiGraph | None = None,
    ) -> nx.DiGraph:
        """Same as build_graph but returns the NetworkX graph directly.

        If `graph` is provided, triples are merged into it (incremental build).
        Otherwise a new graph is created.
        """
        if graph is None:
            graph = nx.DiGraph()
        result = await self.build_graph(tenant_id=tenant_id, chunks=chunks)
        for triple in result["triples"]:
            subj = triple.get("subject", "").strip()
            obj = triple.get("object", "").strip()
            if not subj or not obj:
                continue
            graph.add_node(subj, label=subj)
            graph.add_node(obj, label=obj)
            graph.add_edge(
                subj, obj,
                relation=triple.get("relation", ""),
                chunk_id=triple.get("_chunk_id", ""),
            )
        return graph

    # ------------------------------------------------------------------
    # Query phase
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        *,
        query: str,
        graph: nx.DiGraph,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Given a query and a knowledge graph, return the most relevant
        graph-context chunks.
        """
        entities = await self._extract_query_entities(query)
        if not entities:
            return []

        # Find seed nodes (case-insensitive match).
        seed_nodes: list[str] = []
        graph_lower = {n.lower(): n for n in graph.nodes()}
        for entity in entities:
            lower = entity.lower()
            if lower in graph_lower:
                seed_nodes.append(graph_lower[lower])
            else:
                for glower, gnode in graph_lower.items():
                    if lower in glower and gnode not in seed_nodes:
                        seed_nodes.append(gnode)

        if not seed_nodes:
            return []

        # BFS traversal.
        visited: set[str] = set(seed_nodes)
        frontier: set[str] = set(seed_nodes)
        collected: list[tuple[str, str, dict[str, Any]]] = []

        for _ in range(self._max_hops):
            next_frontier: set[str] = set()
            for node in frontier:
                for _, nb, data in graph.out_edges(node, data=True):
                    collected.append((node, nb, data))
                    if nb not in visited:
                        visited.add(nb)
                        next_frontier.add(nb)
                for pred, _, data in graph.in_edges(node, data=True):
                    collected.append((pred, node, data))
                    if pred not in visited:
                        visited.add(pred)
                        next_frontier.add(pred)
            frontier = next_frontier
            if not frontier:
                break

        # Group by chunk_id.
        chunk_map: dict[str, list[str]] = {}
        for src, dst, data in collected:
            cid = data.get("chunk_id", "")
            if not cid:
                continue
            rel = f"{src} → {data.get('relation', 'related_to')} → {dst}"
            chunk_map.setdefault(cid, []).append(rel)

        results: list[dict[str, Any]] = []
        for cid, relations in chunk_map.items():
            results.append({
                "chunk_id": cid,
                "retrieval_method": "graph_rag",
                "relations": relations[:top_k],
                "graph_triplets": [
                    {"subject": src, "relation": data.get("relation", ""), "object": dst}
                    for src, dst, data in collected
                    if data.get("chunk_id") == cid
                ][:10],
            })
        return results[:top_k]

    async def summarize_subgraph(
        self,
        query: str,
        triples: list[dict[str, str]],
    ) -> str:
        """Generate a natural-language summary of a subgraph."""
        triplets_text = "\n".join(
            f"- {t.get('subject', '?')} → {t.get('relation', '?')} → {t.get('object', '?')}"
            for t in triples
        )
        response = await self._llm(
            _GRAPH_SUMMARY_SYSTEM,
            f"Question: {query}\n\nKnowledge graph triplets:\n{triplets_text}",
        )
        return response.strip()

    # ------------------------------------------------------------------
    # Internal: LLM calls via callback
    # ------------------------------------------------------------------

    async def _extract_triples(self, text: str) -> list[dict[str, str]]:
        response = await self._llm(
            _EXTRACT_TRIPLES_SYSTEM,
            f"Extract triples from this text:\n\n{text}",
        )
        return _safe_json_parse(response, default=[])

    async def _extract_query_entities(self, query: str) -> list[str]:
        response = await self._llm(_QUERY_ENTITIES_SYSTEM, query)
        return _safe_json_parse(response, default=[])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_json_parse(text: str, *, default: Any) -> Any:
    """Extract a JSON array from LLM output (handles markdown fences)."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: text.rstrip().rfind("```")]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return default
