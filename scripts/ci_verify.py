#!/usr/bin/env python3
"""Check CI status for the latest commit on the current branch.

Usage:
    python scripts/ci_check.py              # Check latest commit CI
    python scripts/ci_check.py --wait 120   # Wait up to 120s for CI to finish
    python scripts/ci_check.py --json       # Output JSON for scripting

Exit codes:
    0 = all checks passed
    1 = CI failed
    2 = CI still running (only with --wait timeout)
    3 = no CI runs found
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def _gh(*args: str) -> str:
    """Run gh CLI and return stdout, raise on failure."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"gh {' '.join(args)} failed: {result.stderr}", file=sys.stderr)
        sys.exit(3)
    return result.stdout.strip()


def get_latest_commit_sha() -> str:
    """Get the SHA of the latest commit on the current branch."""
    return _gh("rev-parse", "HEAD")


def get_check_runs(sha: str) -> list[dict]:
    """Get all check runs for a commit."""
    raw = _gh("api", f"/repos/{owner}/{repo}/commits/{sha}/check-runs")
    data = json.loads(raw)
    return data.get("check_runs", [])


def check_status(sha: str) -> dict:
    """Return summary of CI status for a commit.

    Returns dict with:
        conclusion: "success" | "failure" | "pending" | "none"
        total: int
        passed: int
        failed: int
        pending: int
        details: list of {name, status, conclusion, url}
    """
    runs = get_check_runs(sha)
    if not runs:
        return {
            "conclusion": "none",
            "total": 0,
            "passed": 0,
            "failed": 0,
            "pending": 0,
            "details": [],
        }

    passed = sum(1 for r in runs if r["conclusion"] == "success")
    failed = sum(1 for r in runs if r["conclusion"] in ("failure", "timed_out", "cancelled"))
    pending = sum(1 for r in runs if r["status"] == "in_progress" or r["conclusion"] is None)

    if pending > 0:
        conclusion = "pending"
    elif failed > 0:
        conclusion = "failure"
    elif passed == len(runs):
        conclusion = "success"
    else:
        conclusion = "pending"

    details = [
        {
            "name": r["name"],
            "status": r["status"],
            "conclusion": r.get("conclusion"),
            "url": r.get("html_url", ""),
        }
        for r in runs
    ]

    return {
        "conclusion": conclusion,
        "total": len(runs),
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "details": details,
    }


def format_summary(status: dict) -> str:
    """Format a human-readable CI status summary."""
    lines = []
    icon = {"success": "✅", "failure": "❌", "pending": "⏳", "none": "⚠️"}
    failed_str = f", {status['failed']} failed" if status["failed"] else ""
    pending_str = f", {status['pending']} pending" if status["pending"] else ""
    lines.append(
        f"{icon.get(status['conclusion'], '❓')} "
        f"{status['passed']}/{status['total']} passed"
        f"{failed_str}"
        f"{pending_str}"
    )
    for d in status["details"]:
        c = d["conclusion"] or "pending"
        mark = {"success": "✅", "failure": "❌", "pending": "⏳", "skipped": "⏭️"}.get(c, "❓")
        lines.append(f"  {mark} {d['name']}")
        if d["conclusion"] == "failure" and d.get("url"):
            lines.append(f"     {d['url']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check CI status for latest commit")
    parser.add_argument("--wait", type=int, default=0, help="Max seconds to wait for CI to finish")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--sha", type=str, help="Commit SHA (default: HEAD)")
    args = parser.parse_args()

    sha = args.sha or get_latest_commit_sha()
    print(f"🔍 Checking CI for {sha[:7]}...", file=sys.stderr)

    if args.wait > 0:
        deadline = time.time() + args.wait
        while time.time() < deadline:
            status = check_status(sha)
            if status["conclusion"] in ("success", "failure"):
                break
            print(f"  ⏳ CI still running... ({status['pending']} pending)", file=sys.stderr)
            time.sleep(10)
        else:
            print("⏰ Timeout waiting for CI to finish", file=sys.stderr)
            if args.json:
                print(json.dumps(status, indent=2))
            sys.exit(2)

    status = check_status(sha)

    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(format_summary(status))

    if status["conclusion"] == "success":
        sys.exit(0)
    elif status["conclusion"] == "failure":
        sys.exit(1)
    elif status["conclusion"] == "pending":
        sys.exit(2)
    else:
        sys.exit(3)


# Resolve owner/repo from git remote
_remote = subprocess.run(
    ["git", "remote", "get-url", "origin"],
    capture_output=True, text=True, timeout=10,
).stdout.strip()

# Parse "git@github.com:owner/repo.git" or "https://github.com/owner/repo.git"
if "github.com" in _remote:
    _remote = _remote.split("github.com")[-1].strip(":/")
    _remote = _remote.removesuffix(".git")
    owner, repo = _remote.split("/")
else:
    owner, repo = "chyinan", "AegisRAG"


if __name__ == "__main__":
    main()
