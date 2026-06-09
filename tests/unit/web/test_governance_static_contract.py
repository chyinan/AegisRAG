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


def test_governance_js_exports_safe_allowlists_without_forbidden_fields() -> None:
    js = _read_asset("sidecar.js")

    assert "GOVERNANCE_SAFE_FIELDS" in js
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
