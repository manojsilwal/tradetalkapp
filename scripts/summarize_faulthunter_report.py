#!/usr/bin/env python3
"""
Parse a FaultHunter Markdown report (daily or latest) and emit JSON or Markdown summary.

Usage:
  python scripts/summarize_faulthunter_report.py report.md --json
  python scripts/summarize_faulthunter_report.py report.md --markdown
  curl -fsSL "$FAULTHUNTER_REPORT_URL" | python scripts/summarize_faulthunter_report.py - --markdown
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def _parse_header_meta(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- Run ID:"):
            m = re.search(r"`([^`]+)`", line)
            if m:
                meta["run_id"] = m.group(1)
        elif line.startswith("- Profile:"):
            m = re.search(r"`([^`]+)`", line)
            if m:
                meta["profile"] = m.group(1)
        elif line.startswith("- Target:"):
            m = re.search(r"`([^`]*)`", line)
            if m:
                meta["target"] = m.group(1)
        elif line.startswith("- Findings:"):
            meta["findings_line"] = line
    return meta


def _parse_summary_table(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_summary = False
    header_seen = False
    for line in text.splitlines():
        if line.strip() == "## Summary":
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            break
        if not in_summary:
            continue
        if not line.strip().startswith("|"):
            continue
        if "Test" in line and "Feature" in line:
            header_seen = True
            continue
        if not header_seen or re.match(r"^\|\s*---", line):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 7:
            continue
        test_id = parts[0].strip("`")
        feature = parts[1].strip("`")
        verdict = parts[2].strip("`")
        severity = parts[3].strip("`")
        rows.append(
            {
                "test_id": test_id,
                "feature": feature,
                "verdict": verdict,
                "severity": severity,
            }
        )
    return rows


def _parse_findings_sections(text: str) -> list[dict[str, Any]]:
    """Split on ### <test-id> - <feature> and extract bullet fields."""
    findings: list[dict[str, Any]] = []
    m = re.search(r"^## Findings\s*$", text, re.MULTILINE)
    if not m:
        return findings
    rest = text[m.end() :]
    for match in re.finditer(r"(?m)^### (.+)$", rest):
        title = match.group(1).strip()
        start = match.end()
        next_heading = re.search(r"(?m)^### ", rest[start:])
        end = start + next_heading.start() if next_heading else len(rest)
        body = rest[start:end].strip()
        tm = re.match(r"^([^\s]+)\s*-\s*(.+)$", title)
        test_id = tm.group(1).strip() if tm else title
        feature = tm.group(2).strip() if tm else ""
        record: dict[str, Any] = {
            "test_id": test_id,
            "feature": feature,
            "fields": {},
        }
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            kv = line[2:].split(":", 1)
            if len(kv) != 2:
                continue
            key = kv[0].strip().lower().replace(" ", "_")
            val = kv[1].strip()
            record["fields"][key] = val
        findings.append(record)
    return findings


def summarize(text: str) -> dict[str, Any]:
    meta = _parse_header_meta(text)
    summary_rows = _parse_summary_table(text)
    findings = _parse_findings_sections(text)
    failing = [r for r in summary_rows if r.get("verdict", "").lower() != "pass"]
    return {
        "meta": meta,
        "summary_table": summary_rows,
        "failing_rows": failing,
        "findings": findings,
    }


def _markdown_out(data: dict[str, Any]) -> str:
    lines = [
        "## FaultHunter report summary",
        "",
        f"- Run ID: `{data['meta'].get('run_id', 'n/a')}`",
        f"- Profile: `{data['meta'].get('profile', 'n/a')}`",
        f"- Target: `{data['meta'].get('target', 'n/a')}`",
        "",
        "### Failing or non-pass rows",
        "",
    ]
    if not data["failing_rows"]:
        lines.append("- *(none — all summary rows pass)*")
    else:
        for r in data["failing_rows"]:
            lines.append(
                f"- **`{r['test_id']}`** (`{r['feature']}`) — verdict `{r['verdict']}`, severity `{r['severity']}`"
            )
    lines.extend(["", "### Findings detail", ""])
    for f in data["findings"]:
        tid = f["test_id"]
        issue = f["fields"].get("issue", "")
        fix = f["fields"].get("recommended_fix", "")
        lines.append(f"#### `{tid}`")
        lines.append("")
        if issue:
            lines.append(f"- **Issue:** {issue}")
        if fix:
            lines.append(f"- **Recommended fix:** {fix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a FaultHunter Markdown report.")
    parser.add_argument(
        "path",
        nargs="?",
        default="-",
        help="Path to .md report, or - for stdin (default: -)",
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    g.add_argument("--markdown", action="store_true", help="Emit Markdown checklist to stdout")
    args = parser.parse_args()

    text = _read_text(args.path)
    data = summarize(text)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(_markdown_out(data), end="")


if __name__ == "__main__":
    main()
