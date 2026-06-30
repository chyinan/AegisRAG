from __future__ import annotations

import subprocess
from pathlib import Path

SIDECAR_ROOT = Path("apps/web/sidecar")
BEHAVIOR_RUNNER = Path("tests/unit/web/sidecar_behavior_runner.js")


def _read_asset(name: str) -> str:
    return (SIDECAR_ROOT / name).read_text(encoding="utf-8")


def test_sidecar_html_declares_three_views_and_accessibility_regions() -> None:
    html = _read_asset("index.html")

    assert 'role="tablist"' in html
    assert html.count('role="tab"') >= 3
    assert 'data-view="source"' in html
    assert 'data-view="status"' in html
    assert 'data-view="diagnostics"' in html
    assert 'aria-live="polite"' in html
    assert 'role="alert"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'id="inspector-title"' in html


def test_sidecar_html_remains_source_inspector_first_without_governance_shell() -> None:
    html = _read_asset("index.html")

    assert "<title>AegisRAG Source Inspector</title>" in html
    assert '<h1 id="page-title">AegisRAG Source Inspector</h1>' in html
    assert "AegisRAG Governance Workbench" not in html
    assert 'aria-label="Governance workbench views"' not in html
    assert 'id="governance-scope"' not in html
    assert 'aria-live="polite"' in html
    assert "data-governance-view" not in html
    assert "evidence link" in html

    forbidden_shell_fragments = [
        "bearer token",
        "full query",
        "full prompt",
        "full chunk",
        "provider payload",
    ]
    for fragment in forbidden_shell_fragments:
        assert fragment not in html.lower()


def test_sidecar_js_declares_governance_safe_field_allowlists() -> None:
    js = _read_asset("sidecar.js")

    assert "GOVERNANCE_VIEWS" in js
    assert "GOVERNANCE_SAFE_FIELDS" in js
    for field in (
        "tenant_id",
        "user_id",
        "request_id",
        "trace_id",
        "document_id",
        "version_id",
        "chunk_id",
        "status",
        "failure_stage",
        "error_code",
        "result_count",
        "citation_count",
        "agent_run_id",
        "tool_call_id",
        "report_filename",
        "dataset_version",
        "failed_count",
        "token_usage",
        "safe_summary",
        "safe_counts",
        "agent_run_id",
        "tool_name",
        "permission",
        "export_id",
        "item_count",
        "candidate_id",
        "source_review_item_id",
        "requires_human_confirmation",
        "tool_event_count",
        "tool_result_count",
        "tool_error_count",
        "next_step",
        "audit_ref",
        "review_ref",
    ):
        assert f'"{field}"' in js

    forbidden_response_fields = [
        "source_uri",
        "object_key",
        "full_query",
        "prompt",
        "chunk_content",
        "sql",
        "vectors",
        "embeddings",
        "provider_raw_response",
        "token",
        "access_token",
        "secret",
        "raw_exception",
        "tool_observation",
        "raw_arguments",
        "raw_output",
    ]
    for field in forbidden_response_fields:
        assert f'"{field}"' not in js

    assert "SAFE_TOOL_EVENT_FIELDS" in js
    assert "SAFE_TOOL_EVENT_METADATA_FIELDS" in js
    assert "parseToolEventFallbackForTest" in js
    assert "renderToolEventFallbackForTest" in js


def test_sidecar_html_accepts_only_allowed_citation_inputs() -> None:
    html = _read_asset("index.html")

    for field in (
        "document_id",
        "version_id",
        "chunk_id",
        "page_start",
        "page_end",
        "request_id",
        "citation_ref",
    ):
        assert f'name="{field}"' in html

    forbidden_fields = ["tenant_id", "user_id", "acl", "source_uri", "object_key", "prompt"]
    for field in forbidden_fields:
        assert f'name="{field}"' not in html


def test_sidecar_js_uses_authoritative_backend_endpoints_and_safe_payload_fields() -> None:
    js = _read_asset("sidecar.js")

    assert "CITATION_INPUT_FIELDS" in js
    assert '"/sources/resolve"' in js
    assert '"/diagnostics/resolve"' in js
    assert '"POST"' in js
    assert '"/documents/"' in js
    assert '"/versions/"' in js
    assert '"/status"' in js
    assert "encodeURIComponent" in js

    allowed_fields = [
        "document_id",
        "version_id",
        "chunk_id",
        "page_start",
        "page_end",
        "request_id",
        "citation_ref",
    ]
    for field in allowed_fields:
        assert f'"{field}"' in js

    assert '"acl"' not in js


def test_sidecar_diagnostics_declares_lookup_form_endpoint_and_safe_fields_only() -> None:
    html = _read_asset("index.html")
    js = _read_asset("sidecar.js")

    assert 'id="diagnostics-form"' in html
    assert 'id="diagnostics-result"' in html
    assert 'id="diagnostics-stages"' in html
    assert 'id="copy-diagnostics-report"' in html
    assert 'id="download-diagnostics-report"' in html
    assert "SAFE_DIAGNOSTICS_SUMMARY_FIELDS" in js
    assert "SAFE_DIAGNOSTICS_STAGE_FIELDS" in js
    assert "SAFE_DIAGNOSTICS_TIMELINE_FIELDS" in js
    assert "SAFE_DIAGNOSTICS_COUNT_FIELDS" in js
    assert "SAFE_DIAGNOSTICS_REPORT_FIELDS" in js
    assert "SAFE_EVAL_REPORT_SUMMARY_FIELDS" in js
    assert "SAFE_EVAL_CASE_FIELDS" in js
    assert "SAFE_EVAL_GATE_FIELDS" in js
    assert "SAFE_EVAL_REPORT_EXPORT_FIELDS" in js
    assert "SAFE_AUDIT_LOG_FIELDS" in js
    assert "SAFE_AUDIT_ASSOCIATION_FIELDS" in js
    assert "SAFE_AUDIT_EXPORT_FIELDS" in js
    assert "SAFE_AUDIT_COUNT_FIELDS" in js
    assert "SAFE_REVIEW_ITEM_FIELDS" in js
    assert "SAFE_REVIEW_IDENTIFIER_FIELDS" in js
    assert "SAFE_REVIEW_SUMMARY_FIELDS" in js
    assert "SAFE_REVIEW_STATUS_HISTORY_FIELDS" in js
    assert "SAFE_EVAL_CANDIDATE_FIELDS" in js
    assert '"/eval/reports"' in js
    assert '"/diagnostics/resolve"' in js
    assert '"/audit/logs"' in js
    assert '"/audit/export"' in js
    assert '"/review/items"' in js

    for field in (
        "tenant_id",
        "user_id",
        "request_id",
        "trace_id",
        "status",
        "failure_stage",
        "error_code",
        "top_k",
        "result_count",
        "highest_rerank_score",
        "sparse_top_k",
        "deduped_count",
        "threshold_decision",
        "citation_count",
        "latency_ms",
    ):
        assert f'"{field}"' in js

    forbidden_diagnostics_fields = [
        "query_text",
        "answer_text",
        "chunk_content",
        "provider_raw_response",
        "object_key",
        "raw_exception",
    ]
    for field in forbidden_diagnostics_fields:
        assert f'"{field}"' not in js


def test_sidecar_js_never_persists_tokens_or_authorized_excerpts() -> None:
    js = _read_asset("sidecar.js")

    forbidden = [
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "history.pushState",
        "history.replaceState",
        "console.log",
        "text_excerpt: payload",
    ]
    for fragment in forbidden:
        assert fragment not in js


def test_sidecar_js_renders_safe_source_and_status_fields_only() -> None:
    js = _read_asset("sidecar.js")

    source_fields = [
        "source_display_name",
        "source_type",
        "document_id",
        "version_id",
        "chunk_id",
        "page_start",
        "page_end",
        "title_path",
        "text_excerpt",
        "excerpt_char_count",
        "token_count",
        "retrieval_method",
        "score",
        "request_id",
        "trace_id",
    ]
    status_fields = [
        "status",
        "chunk_count",
        "embedding_provider",
        "embedding_model",
        "embedding_version",
        "embedding_dim",
        "vector_count",
        "index_status",
        "job_id",
        "attempt_count",
        "last_attempt_at",
        "next_retry_at",
        "error_code",
        "error_summary",
        "request_id",
        "trace_id",
    ]
    for field in source_fields + status_fields:
        assert f'"{field}"' in js

    forbidden_response_fields = [
        "source_uri",
        "object_key",
        "raw_source_path",
        "full_chunk",
        "prompt",
        "sql",
        "vectors",
        "embeddings",
        "provider_raw_response",
    ]
    for field in forbidden_response_fields:
        assert f'"{field}"' not in js


def test_sidecar_js_maps_all_document_statuses_without_color_only_state() -> None:
    js = _read_asset("sidecar.js")

    for status in (
        "uploaded",
        "parsing",
        "parsed",
        "chunking",
        "chunked",
        "embedding",
        "embedded",
        "indexing",
        "retrieval_ready",
        "failed_retryable",
        "failed_terminal",
        "deleted",
    ):
        assert f'"{status}"' in js

    assert "statusIcon" in js
    assert "statusLabel" in js


def test_sidecar_css_supports_responsive_sheet_focus_and_long_ids() -> None:
    css = _read_asset("sidecar.css")

    assert "@media (max-width: 767px)" in css
    assert ":focus-visible" in css
    assert "overflow-wrap: anywhere" in css
    assert ".id-value" in css
    assert ".inspector-sheet" in css
    assert "max-height" in css


def _run_sidecar_behavior_test(name: str) -> None:
    subprocess.run(
        ["node", str(BEHAVIOR_RUNNER), name],
        check=True,
        capture_output=True,
        text=True,
    )


def test_sidecar_behavior_clears_stale_source_results_on_safe_failure() -> None:
    _run_sidecar_behavior_test("testSafeFailureClearsStaleSourceResults")


def test_sidecar_behavior_does_not_invent_trace_id_from_request_id() -> None:
    _run_sidecar_behavior_test("testSafeFailureDoesNotInventTraceIdFromRequestId")


def test_sidecar_behavior_keeps_status_failure_copy_handlers() -> None:
    _run_sidecar_behavior_test("testStatusFailureCopyButtonKeepsHandler")


def test_sidecar_behavior_omits_invalid_page_bounds_from_payload() -> None:
    _run_sidecar_behavior_test("testInvalidPageInputDoesNotSendNullPageBounds")


def test_sidecar_behavior_traps_tab_focus_inside_inspector_dialog() -> None:
    _run_sidecar_behavior_test("testDialogTrapKeepsTabFocusInsideInspector")


def test_sidecar_behavior_unknown_status_is_not_rendered_as_working() -> None:
    _run_sidecar_behavior_test("testUnknownStatusIsNotRenderedAsWorking")


def test_sidecar_behavior_reports_clipboard_unavailable() -> None:
    _run_sidecar_behavior_test("testClipboardFallbackReportsUnavailableCopy")


def test_sidecar_behavior_diagnostics_lookup_uses_safe_payload() -> None:
    _run_sidecar_behavior_test("testDiagnosticsLookupUsesSafePayload")


def test_sidecar_behavior_diagnostics_failure_renders_only_ids_and_stage() -> None:
    _run_sidecar_behavior_test("testDiagnosticsFailureRendersOnlySafeDetails")


def test_sidecar_behavior_diagnostics_report_export_uses_allowlisted_report() -> None:
    _run_sidecar_behavior_test("testDiagnosticsReportExportUsesAllowlist")


def test_sidecar_behavior_diagnostics_next_steps_clear_stale_commands() -> None:
    _run_sidecar_behavior_test("testDiagnosticsNextStepsClearsStaleCommands")


def test_sidecar_behavior_sync_diagnostics_does_not_auto_lookup() -> None:
    _run_sidecar_behavior_test("testSyncDiagnosticsDoesNotAutoLookup")


def test_sidecar_behavior_governance_navigation_switches_views() -> None:
    _run_sidecar_behavior_test("testGovernanceNavigationSwitchesViews")


def test_sidecar_behavior_governance_failure_clears_stale_panel() -> None:
    _run_sidecar_behavior_test("testGovernanceFailureClearsStalePanel")


def test_sidecar_behavior_source_evidence_parses_service_token_evidence_links() -> None:
    _run_sidecar_behavior_test("testSourceEvidenceParsesServiceTokenEvidenceLinks")


def test_sidecar_behavior_source_evidence_malformed_link_clears_results() -> None:
    _run_sidecar_behavior_test("testSourceEvidenceMalformedEvidenceLinkClearsResults")


def test_sidecar_behavior_tool_event_fallback_uses_safe_allowlist() -> None:
    _run_sidecar_behavior_test("testToolEventFallbackUsesSafeAllowlist")


def test_sidecar_behavior_tool_event_fallback_clears_stale_state_on_malformed_input() -> None:
    _run_sidecar_behavior_test("testToolEventFallbackMalformedInputClearsStaleState")
