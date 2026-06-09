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
    ):
        assert fragment in html


def test_governance_js_exports_safe_allowlists_without_forbidden_fields() -> None:
    js = _read_asset("sidecar.js")

    assert "GOVERNANCE_SAFE_FIELDS" in js
    assert "SAFE_SOURCE_EVIDENCE_FIELDS" in js
    assert "SOURCE_EVIDENCE_MAX_ITEMS" in js
    assert "parseSourceEvidenceInputForTest" in js
    assert "resolveSourceEvidenceSetForTest" in js
    assert "renderSourceEvidenceSetForTest" in js
    assert "copySourceEvidenceSummaryForTest" in js
    assert "SAFE_DOCUMENT_REVIEW_FIELDS" in js
    assert "SAFE_DOCUMENT_REVIEW_DETAIL_FIELDS" in js
    assert "SAFE_DOCUMENT_REVIEW_LIFECYCLE_FIELDS" in js
    assert "renderGovernanceFailureForTest" in js
    for field in (
        "tenant_id",
        "user_id",
        "request_id",
        "trace_id",
        "failure_stage",
        "error_code",
        "agent_run_id",
        "tool_call_id",
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
