import asyncio
import csv
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import websockets

CASE_FILE = Path(__file__).parent / "phase10_cases.csv"
REPORT_JSON = Path(__file__).parent / "phase10_live_report.json"
REPORT_MD = Path(__file__).parent / "phase10_live_report.md"

WS_URL = os.getenv("PHASE10_WS_URL", "ws://localhost:8000/api/chat/stream")
MODEL = os.getenv("PHASE10_MODEL", "qwen2.5:14b-instruct")
PROVIDER = os.getenv("PHASE10_PROVIDER", "ollama")
CASE_TIMEOUT = float(os.getenv("PHASE10_CASE_TIMEOUT", "60"))
RECV_IDLE_TIMEOUT = float(os.getenv("PHASE10_RECV_IDLE_TIMEOUT", "60"))
RUN_TIMEOUT = float(os.getenv("PHASE10_RUN_TIMEOUT", "180"))

DEFAULT_SKIP_TAGS = {"mock_only", "side_effect"}


@dataclass
class Case:
    test_id: str
    query: str
    expected_phase: str
    expected_tool_required: bool
    expected_domains: List[str]
    expected_tool_call_cap: int
    expected_tool_name: str
    expected_status_contains: str
    coverage_tags: List[str]


def parse_cases() -> List[Case]:
    cases = []
    with CASE_FILE.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tags = [t for t in (row.get("coverage_tags") or "").split("|") if t]
            cases.append(
                Case(
                    test_id=row["test_id"],
                    query=row["query"],
                    expected_phase=row["expected_phase"],
                    expected_tool_required=row["expected_tool_required"].lower() == "true",
                    expected_domains=[d for d in (row.get("expected_domains") or "").split(",") if d],
                    expected_tool_call_cap=int(row.get("expected_tool_call_cap") or 0),
                    expected_tool_name=row.get("expected_tool_name") or "",
                    expected_status_contains=row.get("expected_status_contains") or "",
                    coverage_tags=tags,
                )
            )
    return cases


def bar(label: str, count: int, total: int) -> str:
    width = 30
    filled = int(width * (count / total)) if total else 0
    return f"{label:<14} | {'#'*filled}{'.'*(width-filled)} | {count}/{total}"


def should_skip(case: Case, skip_tags: set, include_tags: set) -> bool:
    tag_set = set(case.coverage_tags)
    if include_tags:
        return tag_set.isdisjoint(include_tags)
    return bool(tag_set.intersection(skip_tags))


async def run_case(case: Case) -> Dict[str, Any]:
    tool_calls = []
    statuses = []
    final_content = ""
    errors = []

    payload = {
        "messages": [{"role": "user", "content": case.query}],
        "model": MODEL,
        "provider": PROVIDER,
    }

    try:
        async with websockets.connect(WS_URL, close_timeout=5, ping_interval=None) as ws:
            await ws.send(json.dumps(payload))
            started = asyncio.get_event_loop().time()
            while True:
                if asyncio.get_event_loop().time() - started > RUN_TIMEOUT:
                    errors.append({"error": "Case run timeout"})
                    break
                raw = await asyncio.wait_for(ws.recv(), timeout=RECV_IDLE_TIMEOUT)
                data = json.loads(raw)
                if "error" in data:
                    errors.append(data)
                    break
                if data.get("status"):
                    statuses.append(data.get("status"))
                if data.get("tool_calls"):
                    for tc in data["tool_calls"]:
                        name = tc.get("function", {}).get("name")
                        if name:
                            tool_calls.append(name)
                        # Phase 10 format might just put "name" at the top level of the tool call dict
                        elif "name" in tc:
                            tool_calls.append(tc["name"])
                if data.get("content"):
                    final_content += data.get("content")
                if data.get("done"):
                    break
    except Exception as e:
        errors.append({"error": str(e), "type": type(e).__name__, "repr": repr(e)})

    result = {
        "test_id": case.test_id,
        "query": case.query,
        "expected_tool_required": case.expected_tool_required,
        "expected_tool_call_cap": case.expected_tool_call_cap,
        "expected_tool_name": case.expected_tool_name,
        "tool_calls_detected": tool_calls,
        "status_messages": statuses,
        "errors": errors,
        "final_content": final_content[:200],
        "coverage_tags": case.coverage_tags,
    }

    checks = []
    if errors:
        result["passed"] = False
        result["checks"] = []
        return result

    if case.expected_tool_required:
        checks.append(len(tool_calls) >= 1)
    else:
        checks.append(len(tool_calls) == 0)

    if case.expected_tool_call_cap and case.expected_phase == "MEDIUM":
        checks.append(len(tool_calls) <= case.expected_tool_call_cap)

    if case.expected_tool_name:
        checks.append(case.expected_tool_name in tool_calls)

    if case.expected_status_contains:
        checks.append(any(case.expected_status_contains in s for s in statuses))

    result["passed"] = all(checks)
    result["checks"] = checks
    return result


async def main():
    cases = parse_cases()
    skip_tags = set(filter(None, os.getenv("PHASE10_SKIP_TAGS", "").split(",")))
    include_tags = set(filter(None, os.getenv("PHASE10_INCLUDE_TAGS", "").split(",")))
    if not skip_tags:
        skip_tags = DEFAULT_SKIP_TAGS

    limit = int(os.getenv("PHASE10_LIMIT", "0"))
    filtered = [c for c in cases if not should_skip(c, skip_tags, include_tags)]
    if limit:
        filtered = filtered[:limit]

    results = []
    for case in filtered:
        results.append(await run_case(case))

    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    report = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "results": results,
        "skip_tags": sorted(skip_tags),
        "include_tags": sorted(include_tags),
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# Phase 10.0 Live Orchestrator Eval Report",
        "",
        f"WS URL: {WS_URL}",
        f"Model: {MODEL}",
        f"Provider: {PROVIDER}",
        f"Total: {passed}/{total} passed",
        "",
    ]

    lines.append("## Tag Breakdown")
    tag_counts = {}
    tag_pass = {}
    for r in results:
        for tag in r.get("coverage_tags") or []:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            if r["passed"]:
                tag_pass[tag] = tag_pass.get(tag, 0) + 1
    for tag in sorted(tag_counts.keys()):
        lines.append(bar(tag, tag_pass.get(tag, 0), tag_counts[tag]))

    lines.append("")
    lines.append("## Failures")
    for r in results:
        if not r["passed"]:
            lines.append(f"- {r['test_id']}: {r['query']} -> tools={r['tool_calls_detected']} errors={r['errors']}")

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
