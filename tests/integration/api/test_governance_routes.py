from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app


def test_governance_entrypoint_serves_static_workbench_shell_without_auth() -> None:
    client = TestClient(app)

    response = client.get("/governance")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "AegisRAG Governance Workbench" in response.text
    for view in (
        "document-review",
        "source-evidence",
        "retrieval-diagnostics",
        "eval-evidence",
        "audit-explorer",
        "review-queue",
    ):
        assert f'data-governance-view="{view}"' in response.text
    assert "/sidecar/assets/sidecar.css" in response.text
    assert "/sidecar/assets/sidecar.js" in response.text


def test_governance_entrypoint_reuses_sidecar_assets_without_unsafe_fragments() -> None:
    client = TestClient(app)

    rendered = "\n".join(
        [
            client.get("/governance").text,
            client.get("/sidecar/assets/sidecar.css").text,
            client.get("/sidecar/assets/sidecar.js").text,
        ]
    )

    forbidden_fragments = [
        "source_uri",
        "object_key",
        "localStorage",
        "sessionStorage",
        "prompt_text",
        "answer_text",
        "chunk_content",
        "embedding_vector",
        "provider_raw_response",
        "raw_exception",
        "C:\\",
        "/home/",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in rendered
