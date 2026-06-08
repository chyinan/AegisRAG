from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app


def test_sidecar_entrypoint_serves_static_shell_without_auth() -> None:
    client = TestClient(app)

    response = client.get("/sidecar")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "AegisRAG Source Inspector" in response.text
    assert 'data-view="source"' in response.text
    assert 'data-view="status"' in response.text
    assert 'data-view="diagnostics"' in response.text
    assert "/sidecar/assets/sidecar.css" in response.text
    assert "/sidecar/assets/sidecar.js" in response.text


def test_sidecar_static_assets_are_served_with_safe_content_types() -> None:
    client = TestClient(app)

    css = client.get("/sidecar/assets/sidecar.css")
    js = client.get("/sidecar/assets/sidecar.js")

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert ".id-value" in css.text

    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "fetchSourceResolve" in js.text
    assert "fetchDocumentStatus" in js.text


def test_sidecar_assets_do_not_embed_forbidden_source_or_secret_fields() -> None:
    client = TestClient(app)

    rendered = "\n".join(
        [
            client.get("/sidecar").text,
            client.get("/sidecar/assets/sidecar.css").text,
            client.get("/sidecar/assets/sidecar.js").text,
        ]
    )

    forbidden_fragments = [
        "source_uri",
        "object_key",
        "minio",
        "localStorage",
        "sessionStorage",
        "console.log",
        "prompt_text",
        "embedding_vector",
        "provider_raw_response",
        "C:\\",
        "/home/",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in rendered
