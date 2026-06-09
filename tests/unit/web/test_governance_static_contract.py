from __future__ import annotations

import subprocess
from pathlib import Path

GOVERNANCE_ROOT = Path("apps/web/governance")
BEHAVIOR_RUNNER = Path("tests/unit/web/sidecar_behavior_runner.js")


def _read_asset(name: str) -> str:
    path = GOVERNANCE_ROOT / name
    if not path.exists():
        path = Path("apps/web/sidecar") / name
    return path.read_text(encoding="utf-8")


def test_governance_shell_declares_six_stable_entries_and_safe_scope() -> None:
    html = _read_asset("index.html")

    assert "AegisRAG Governance Workbench" in html
    assert 'aria-label="Governance workbench views"' in html
    assert 'id="governance-scope"' in html
    assert 'id="governance-detail"' in html
    assert 'data-governance-link-view="status"' in html
    assert 'data-governance-link-view="source"' in html
    assert 'data-governance-link-view="diagnostics"' in html
    for view in (
        "document-review",
        "source-evidence",
        "retrieval-diagnostics",
        "eval-evidence",
        "audit-explorer",
        "review-queue",
    ):
        assert f'data-governance-view="{view}"' in html


def test_governance_document_review_declares_backend_controls_and_regions() -> None:
    html = _read_asset("index.html")

    for fragment in (
        'id="document-review-form"',
        'id="document-review-status"',
        'id="document-review-limit"',
        'id="document-review-cursor"',
        'id="document-review-document"',
        'id="document-review-version"',
        'id="document-review-list"',
        'id="document-review-detail"',
        'id="document-review-timeline"',
        'aria-live="polite"',
    ):
        assert fragment in html


def test_governance_source_evidence_declares_input_result_and_copy_regions() -> None:
    html = _read_asset("index.html")

    for fragment in (
        'id="source-evidence-form"',
        'id="source-evidence-json"',
        'id="source-evidence-document"',
        'id="source-evidence-version"',
        'id="source-evidence-chunk"',
        'id="source-evidence-page-start"',
        'id="source-evidence-page-end"',
        'id="source-evidence-request"',
        'id="source-evidence-results"',
        'id="source-evidence-errors"',
        'id="copy-source-evidence-summary"',
        'aria-live="polite"',
        "evidence_links",
        "evidence_url",
        "Evidence excerpts and source details are shown only after backend",
        "authorization.",
    ):
        assert fragment in html


def test_governance_retrieval_diagnostics_declares_safe_timeline_regions() -> None:
    html = _read_asset("index.html")

    for fragment in (
        'id="governance-diagnostics-form"',
        'id="governance-diagnostic-request"',
        'id="governance-diagnostic-trace"',
        'id="governance-diagnostics-summary"',
        'id="governance-diagnostics-timeline"',
        'id="governance-diagnostics-next-steps"',
        'id="copy-governance-diagnostics-report"',
        'id="download-governance-diagnostics-report"',
        'aria-live="polite"',
        'role="alert"',
    ):
        assert fragment in html


def test_governance_eval_evidence_declares_authorized_report_regions() -> None:
    html = _read_asset("index.html")

    for fragment in (
        'id="eval-evidence-form"',
        'id="eval-evidence-limit"',
        'id="eval-evidence-report"',
        'id="eval-evidence-refresh"',
        'id="eval-evidence-load"',
        'id="eval-evidence-report-list"',
        'id="eval-evidence-summary"',
        'id="eval-evidence-cases"',
        'id="eval-evidence-next-steps"',
        'id="copy-eval-evidence-report"',
        'id="download-eval-evidence-report"',
        'aria-live="polite"',
        'role="alert"',
    ):
        assert fragment in html

    for forbidden in (
        'name="tenant_id"',
        'name="permissions"',
        "dataset path",
        "local file path",
    ):
        assert forbidden not in html.lower()


def test_governance_audit_explorer_declares_safe_controls_and_regions() -> None:
    html = _read_asset("index.html")

    for fragment in (
        'id="audit-explorer-form"',
        'id="audit-explorer-user"',
        'id="audit-explorer-request"',
        'id="audit-explorer-trace"',
        'id="audit-explorer-action"',
        'id="audit-explorer-resource-type"',
        'id="audit-explorer-resource-id"',
        'id="audit-explorer-status"',
        'id="audit-explorer-created-from"',
        'id="audit-explorer-created-to"',
        'id="audit-explorer-limit"',
        'id="audit-explorer-results"',
        'id="audit-explorer-detail"',
        'id="audit-explorer-next-steps"',
        'id="audit-explorer-copy-export"',
        'id="audit-explorer-download-export"',
        'aria-live="polite"',
        'role="alert"',
    ):
        assert fragment in html

    for forbidden in (
        'name="tenant_id"',
        'name="roles"',
        'name="permissions"',
        "raw sql",
        "metadata key",
        "database path",
    ):
        assert forbidden not in html.lower()


def test_governance_review_queue_declares_safe_controls_regions_and_no_raw_inputs() -> None:
    html = _read_asset("index.html")

    for fragment in (
        'id="review-queue-create-form"',
        'id="review-queue-filter-form"',
        'id="review-queue-create-type"',
        'id="review-queue-create-severity"',
        'id="review-queue-create-source-view"',
        'id="review-queue-create-request"',
        'id="review-queue-create-trace"',
        'id="review-queue-create-document"',
        'id="review-queue-create-version"',
        'id="review-queue-create-chunk"',
        'id="review-queue-filter-type"',
        'id="review-queue-filter-severity"',
        'id="review-queue-filter-status"',
        'id="review-queue-filter-source-view"',
        'id="review-queue-filter-request"',
        'id="review-queue-filter-trace"',
        'id="review-queue-filter-created-from"',
        'id="review-queue-filter-created-to"',
        'id="review-queue-filter-limit"',
        'id="review-queue-selected-id"',
        'id="review-queue-list"',
        'id="review-queue-detail"',
        'id="review-queue-status-history"',
        'id="review-queue-candidate"',
        'id="review-queue-next-steps"',
        'id="review-queue-alert"',
        'role="alert"',
        'aria-live="polite"',
    ):
        assert fragment in html

    for forbidden in (
        'name="tenant_id"',
        'name="created_by"',
        'name="roles"',
        'name="permissions"',
        'name="query"',
        'name="prompt"',
        'name="answer"',
        "dataset path",
        "local filename",
        "raw sql",
    ):
        assert forbidden not in html.lower()


def test_governance_js_exports_safe_allowlists_without_forbidden_fields() -> None:
    js = _read_asset("sidecar.js")

    assert "GOVERNANCE_SAFE_FIELDS" in js
    assert "SAFE_SOURCE_EVIDENCE_FIELDS" in js
    assert "SOURCE_EVIDENCE_MAX_ITEMS" in js
    assert "parseSourceEvidenceInputForTest" in js
    assert "evidence_links" in js
    assert "evidence_url" in js
    assert "evidence_query" in js
    assert "resolveSourceEvidenceSetForTest" in js
    assert "renderSourceEvidenceSetForTest" in js
    assert "copySourceEvidenceSummaryForTest" in js
    assert "SAFE_DOCUMENT_REVIEW_FIELDS" in js
    assert "SAFE_DOCUMENT_REVIEW_DETAIL_FIELDS" in js
    assert "SAFE_DOCUMENT_REVIEW_LIFECYCLE_FIELDS" in js
    assert "SAFE_DIAGNOSTICS_TIMELINE_FIELDS" in js
    assert "SAFE_DIAGNOSTICS_COUNT_FIELDS" in js
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
    assert '"/review/items"' in js
    assert "fetchReviewQueueItemsForTest" in js
    assert "renderReviewQueueListForTest" in js
    assert "copyReviewQueueExportForTest" in js
    assert '"/audit/logs"' in js
    assert '"/audit/export"' in js
    assert "fetchGovernanceDiagnosticsForTest" in js
    assert "fetchEvalEvidenceReportsForTest" in js
    assert "renderGovernanceDiagnosticsResultForTest" in js
    assert "renderGovernanceDiagnosticsFailureForTest" in js
    assert "renderEvalEvidenceReportListForTest" in js
    assert "renderEvalEvidenceDetailForTest" in js
    assert "renderEvalEvidenceFailureForTest" in js
    assert "fetchAuditExplorerLogsForTest" in js
    assert "renderAuditExplorerListForTest" in js
    assert "copyAuditExplorerExportForTest" in js
    assert "renderGovernanceFailureForTest" in js
    assert "SAFE_TOOL_EVENT_FIELDS" in js
    assert "SAFE_TOOL_EVENT_METADATA_FIELDS" in js
    assert "parseToolEventFallbackForTest" in js
    assert "renderToolEventFallbackForTest" in js
    for field in (
        "tenant_id",
        "user_id",
        "request_id",
        "trace_id",
        "failure_stage",
        "error_code",
        "agent_run_id",
        "tool_call_id",
        "tool_event_count",
        "tool_result_count",
        "tool_error_count",
        "next_step",
        "audit_ref",
        "review_ref",
    ):
        assert f'"{field}"' in js

    forbidden_fields = [
        "source_uri",
        "object_key",
        "acl",
        "full_query",
        "chunk_content",
        "provider_raw_response",
        "raw_exception",
        "raw_arguments",
        "raw_output",
        "tool_observation",
    ]
    for field in forbidden_fields:
        assert f'"{field}"' not in js


def test_governance_css_keeps_responsive_tabs_and_long_id_wrapping() -> None:
    css = Path("apps/web/sidecar/sidecar.css").read_text(encoding="utf-8")

    assert ".governance-nav" in css
    assert ".governance-tab" in css
    assert ".source-evidence-controls" in css
    assert ".source-evidence-item" in css
    assert ".source-evidence-meta-grid" in css
    assert ".diagnostics-timeline" in css
    assert ".diagnostics-stage-row" in css
    assert ".eval-evidence-controls" in css
    assert ".eval-report-list" in css
    assert ".eval-metric-grid" in css
    assert ".eval-case-row" in css
    assert ".eval-gate-row" in css
    assert ".audit-explorer-controls" in css
    assert ".audit-log-row" in css
    assert ".audit-association-row" in css
    assert ".audit-export-row" in css
    assert ".audit-count-chip" in css
    assert ".review-queue-controls" in css
    assert ".review-item-row" in css
    assert ".review-status-history-row" in css
    assert ".eval-candidate-row" in css
    assert "repeat(auto-fit, minmax(124px, 1fr))" in css
    assert "@media (max-width: 767px)" in css
    assert "overflow-wrap: anywhere" in css


def _run_governance_behavior_test(name: str) -> None:
    subprocess.run(
        ["node", str(BEHAVIOR_RUNNER), name],
        check=True,
        capture_output=True,
        text=True,
    )


def test_governance_behavior_navigation_switches_views() -> None:
    _run_governance_behavior_test("testGovernanceNavigationSwitchesViews")


def test_governance_behavior_links_backend_views() -> None:
    _run_governance_behavior_test("testGovernanceLinksBackendViews")


def test_governance_behavior_supports_keyboard_tabs() -> None:
    _run_governance_behavior_test("testGovernanceKeyboardTabs")


def test_governance_behavior_failure_clears_stale_panel() -> None:
    _run_governance_behavior_test("testGovernanceFailureClearsStalePanel")


def test_governance_behavior_document_review_renders_safe_list() -> None:
    _run_governance_behavior_test("testDocumentReviewRendersSafeList")


def test_governance_behavior_document_review_failure_clears_stale_regions() -> None:
    _run_governance_behavior_test("testDocumentReviewFailureClearsStaleRegions")


def test_governance_behavior_document_review_missing_id_clears_stale_regions() -> None:
    _run_governance_behavior_test("testDocumentReviewMissingDocumentIdClearsStaleRegions")


def test_governance_behavior_document_review_empty_list_clears_cursor() -> None:
    _run_governance_behavior_test("testDocumentReviewEmptyListClearsCursorAndShowsEmptyState")


def test_governance_behavior_document_review_unknown_status_is_safe() -> None:
    _run_governance_behavior_test("testDocumentReviewUnknownStatusIsSafe")


def test_governance_behavior_source_evidence_parses_citations_safely() -> None:
    _run_governance_behavior_test("testSourceEvidenceParsesCitationsSafely")


def test_governance_behavior_source_evidence_parses_openwebui_evidence_links() -> None:
    _run_governance_behavior_test("testSourceEvidenceParsesOpenWebUIEvidenceLinks")


def test_governance_behavior_source_evidence_malformed_link_clears_results() -> None:
    _run_governance_behavior_test("testSourceEvidenceMalformedEvidenceLinkClearsResults")


def test_governance_behavior_source_evidence_resolves_each_reference() -> None:
    _run_governance_behavior_test("testSourceEvidenceResolvesEachReference")


def test_governance_behavior_source_evidence_clears_stale_before_resolve_finishes() -> None:
    _run_governance_behavior_test("testSourceEvidenceClearsStaleResultsBeforeResolveCompletes")


def test_governance_behavior_source_evidence_denial_clears_stale_item() -> None:
    _run_governance_behavior_test("testSourceEvidenceDenialClearsStaleItem")


def test_governance_behavior_source_evidence_malformed_input_clears_results() -> None:
    _run_governance_behavior_test("testSourceEvidenceMalformedInputClearsResults")


def test_governance_behavior_source_evidence_copy_summary_uses_allowlist() -> None:
    _run_governance_behavior_test("testSourceEvidenceCopySummaryUsesAllowlist")


def test_governance_behavior_diagnostics_lookup_renders_timeline() -> None:
    _run_governance_behavior_test("testGovernanceDiagnosticsLookupRendersTimeline")


def test_governance_behavior_diagnostics_permission_failure_clears_stale_state() -> None:
    _run_governance_behavior_test("testGovernanceDiagnosticsPermissionFailureClearsStaleState")


def test_governance_behavior_diagnostics_new_lookup_clears_report_copy_export() -> None:
    _run_governance_behavior_test("testGovernanceDiagnosticsNewLookupClearsReportCopyExport")


def test_governance_behavior_eval_evidence_renders_report_list() -> None:
    _run_governance_behavior_test("testEvalEvidenceReportListRendering")


def test_governance_behavior_eval_evidence_renders_safe_detail() -> None:
    _run_governance_behavior_test("testEvalEvidenceDetailRenderingUsesAllowlists")


def test_governance_behavior_eval_evidence_permission_failure_clears_stale_state() -> None:
    _run_governance_behavior_test("testEvalEvidencePermissionFailureClearsStaleState")


def test_governance_behavior_eval_evidence_export_uses_allowlist() -> None:
    _run_governance_behavior_test("testEvalEvidenceReportExportUsesAllowlist")


def test_governance_behavior_eval_evidence_tab_switch_does_not_auto_lookup() -> None:
    _run_governance_behavior_test("testEvalEvidenceTabSwitchDoesNotAutoLookup")


def test_governance_behavior_audit_explorer_renders_safe_list() -> None:
    _run_governance_behavior_test("testAuditExplorerListRenderingUsesAllowlists")


def test_governance_behavior_audit_explorer_permission_failure_clears_stale_state() -> None:
    _run_governance_behavior_test("testAuditExplorerPermissionFailureClearsStaleState")


def test_governance_behavior_audit_explorer_export_uses_backend_allowlist() -> None:
    _run_governance_behavior_test("testAuditExplorerBackendExportUsesAllowlist")


def test_governance_behavior_audit_explorer_tab_switch_does_not_auto_lookup() -> None:
    _run_governance_behavior_test("testAuditExplorerTabSwitchDoesNotAutoLookup")


def test_governance_behavior_review_queue_create_renders_safe_payload() -> None:
    _run_governance_behavior_test("testReviewQueueCreateAndListUseSafePayloads")


def test_governance_behavior_review_queue_permission_failure_clears_stale_state() -> None:
    _run_governance_behavior_test("testReviewQueuePermissionFailureClearsStaleState")


def test_governance_behavior_review_queue_candidate_export_uses_allowlist() -> None:
    _run_governance_behavior_test("testReviewQueueCandidatePreviewAndExportUseAllowlists")


def test_governance_behavior_review_queue_tab_switch_does_not_auto_lookup() -> None:
    _run_governance_behavior_test("testReviewQueueTabSwitchDoesNotAutoLookup")


def test_governance_behavior_tool_event_fallback_uses_safe_allowlist() -> None:
    _run_governance_behavior_test("testToolEventFallbackUsesSafeAllowlist")


def test_governance_behavior_tool_event_fallback_clears_stale_state() -> None:
    _run_governance_behavior_test("testToolEventFallbackMalformedInputClearsStaleState")
