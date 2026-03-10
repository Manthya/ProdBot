import asyncio
import csv
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from chatbot_ai_system.models.schemas import ChatMessage, MessageRole, StreamChunk, ToolCall, ToolCallFunction
from chatbot_ai_system.orchestrator import ChatOrchestrator
from chatbot_ai_system.providers.base import BaseLLMProvider


CASE_FILE = Path(__file__).parent / "phase10_cases.csv"
REPORT_JSON = Path(__file__).parent / "phase10_report.json"
REPORT_MD = Path(__file__).parent / "phase10_report.md"


@dataclass
class Case:
    test_id: str
    query: str
    expected_phase: str
    expected_tool_required: bool
    expected_domains: List[str]
    expected_tool_call_cap: int
    router_override: str
    stream_tool_calls: str
    tool_behavior: str
    expected_verification: bool
    verify_outcome: str
    expected_status_contains: str
    expected_tool_name: str
    coverage_tags: List[str]


class FakeTool:
    def __init__(self, name: str, registry: "FakeRegistry"):
        self.name = name
        self.registry = registry

    async def run(self, **kwargs):
        behavior = self.registry.behavior_map.get(self.name, "ok")
        if behavior == "empty":
            return ""
        if behavior == "error":
            raise RuntimeError("simulated tool error")
        if behavior == "large":
            return "X" * 6000
        return f"ok:{self.name}"

    def to_ollama_format(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"Mock tool {self.name}",
                "parameters": {"type": "object", "properties": {}},
            },
        }


class FakeRegistry:
    def __init__(self):
        self.behavior_map: Dict[str, str] = {}
        self._tools: Dict[str, FakeTool] = {}
        self._domain_map: Dict[str, List[str]] = {
            "TIME": ["current_time", "get_timestamp"],
            "GIT": ["git_status", "git_log", "git_branch", "git_diff", "git_checkout"],
            "FETCH": ["fetch_html", "web_search"],
            "FILESYSTEM": ["directory_tree", "read_file", "write_file", "delete_file"],
            "SQLITE": ["query", "execute", "create_table", "insert_record", "drop_table"],
            "MEMORY": ["memory_search"],
            "GENERAL": ["python_sandbox"],
        }
        for names in self._domain_map.values():
            for name in names:
                if name not in self._tools:
                    self._tools[name] = FakeTool(name, self)

    def get_categories(self) -> List[str]:
        return ["GENERAL", "TIME", "GIT", "FETCH", "FILESYSTEM", "SQLITE", "MEMORY"]

    def get_tools_by_category(self, category: str) -> List[Dict[str, Any]]:
        cat = category.upper()
        names = self._domain_map.get(cat, [])
        return [self._tools[name].to_ollama_format() for name in names]

    async def get_ollama_tools(self, query: Optional[str] = None) -> List[Dict[str, Any]]:
        if not query:
            return []
        q = query.lower()
        tools = []
        for name, tool in self._tools.items():
            if any(token in name for token in q.split()):
                tools.append(tool.to_ollama_format())
        return tools[:6]

    def get_tool(self, name: str) -> FakeTool:
        return self._tools[name]

    def set_behavior(self, tool_name: str, behavior: str):
        if tool_name in self._tools:
            self.behavior_map[tool_name] = behavior


class FakeConversationRepo:
    def __init__(self):
        self.messages: List[Dict[str, Any]] = []
        self.session = self

    async def rollback(self):
        return None

    async def add_message(self, **kwargs):
        self.messages.append(kwargs)
        class Obj:
            id = uuid.uuid4()
        return Obj()

    async def get_conversation_summary(self, conversation_id):
        return None

    async def get_recent_messages(self, conversation_id, limit=50):
        return []

    async def update_message_embedding(self, message_id, embedding):
        return None

    async def search_similar_messages(self, user_id, query_embedding, limit=3):
        return []

    async def get_next_sequence_number(self, conversation_id):
        return 1


class FakeMemoryRepo:
    async def get_user_memories(self, user_id):
        return []


class Phase10MockProvider(BaseLLMProvider):
    def __init__(self, case: Case, registry: FakeRegistry):
        self.case = case
        self.registry = registry

    def get_available_models(self) -> List[str]:
        return ["mock-model"]

    async def health_check(self) -> bool:
        return True

    def _try_parse_tool_calls(self, content: str) -> List[ToolCall]:
        return []

    def _make_tool_calls(self, tool_defs: List[Dict[str, Any]], count: int) -> List[ToolCall]:
        tool_names = [t["function"]["name"] for t in tool_defs]
        selected = tool_names[:count]
        if self.case.expected_tool_name and self.case.expected_tool_name in tool_names:
            selected = [self.case.expected_tool_name] + [n for n in tool_names if n != self.case.expected_tool_name]
            selected = selected[:count]
        calls = []
        for i, name in enumerate(selected):
            calls.append(
                ToolCall(
                    id=f"call_{i}",
                    type="function",
                    function=ToolCallFunction(name=name, arguments={}),
                )
            )
        return calls

    async def stream(self, messages, **kwargs):
        tools = kwargs.get("tools")
        # Agentic: if tool results already present, return final text
        if any(getattr(m, "role", None) == MessageRole.TOOL for m in messages):
            yield StreamChunk(content="final answer", done=True)
            return

        if tools:
            mode = self.case.stream_tool_calls
            if mode == "one":
                yield StreamChunk(content="", tool_calls=self._make_tool_calls(tools, 1), done=True)
                return
            if mode == "multi":
                yield StreamChunk(content="", tool_calls=self._make_tool_calls(tools, 2), done=True)
                return
            # none
            yield StreamChunk(content="", done=True)
            return

        # No tools -> plain response
        yield StreamChunk(content="ok", done=True)

    async def complete(self, messages, **kwargs):
        system = messages[0].content if messages else ""
        last = messages[-1].content if messages else ""
        all_text = " ".join([m.content or "" for m in messages if hasattr(m, "content")])

        # Router prompt
        if "routing classifier" in system.lower() or "json object for routing" in system.lower():
            override = self.case.router_override
            if override == "MALFORMED":
                content = "phase: MEDIUM\ntool_required: true\ntool_domains: fetch\nexpected_tool_calls: 1\nconfidence: 0.7"
            elif override == "LOWCONF":
                content = json.dumps({
                    "phase": "MEDIUM",
                    "tool_required": True,
                    "tool_domains": ["fetch"],
                    "expected_tool_calls": 1,
                    "confidence": 0.1,
                    "need_clarification": False,
                })
            elif override:
                content = override
            else:
                content = json.dumps({
                    "phase": self.case.expected_phase,
                    "tool_required": self.case.expected_tool_required,
                    "tool_domains": self.case.expected_domains,
                    "expected_tool_calls": self.case.expected_tool_call_cap,
                    "confidence": 0.8,
                    "need_clarification": False,
                })
            class Resp:
                message = type("M", (), {"content": content})()
            return Resp()

        # Forced tool call retry
        if "call exactly one available tool" in all_text.lower():
            tools = kwargs.get("tools") or []
            calls = self._make_tool_calls(tools, 1)
            class Resp:
                message = type("M", (), {"content": None, "tool_calls": calls})()
                usage = None
            return Resp()

        # Verification prompt
        if "verification assistant" in last.lower():
            ok = True if self.case.verify_outcome != "fail" else False
            content = json.dumps({"ok": ok, "reason": "simulated"})
            class Resp:
                message = type("M", (), {"content": content})()
            return Resp()

        # Planner prompt
        if "task planner" in system.lower():
            class Resp:
                message = type("M", (), {"content": "1. Read files\n2. Summarize"})()
            return Resp()

        # Default
        class Resp:
            message = type("M", (), {"content": "ok"})()
        return Resp()


class InstrumentedOrchestrator(ChatOrchestrator):
    async def _route_request(self, user_input: str, model: str, has_media: bool):
        decision = await super()._route_request(user_input, model, has_media)
        self.last_router_decision = decision
        return decision

    async def _safe_embed(self, message_id: Any, content: str):
        return None

    async def _safe_embed_user(self, conversation_id: uuid.UUID, sequence_number: int):
        return None

    async def _safe_summarize(self, conversation_id: Any, current_seq: int, last_seq: int, model: str):
        return None


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
                    expected_domains=[d for d in row["expected_domains"].split("|") if d] if "|" in row["expected_domains"] else [d for d in row["expected_domains"].split(",") if d],
                    expected_tool_call_cap=int(row["expected_tool_call_cap"] or 0),
                    router_override=row["router_override"],
                    stream_tool_calls=row["stream_tool_calls"],
                    tool_behavior=row["tool_behavior"],
                    expected_verification=row["expected_verification"].lower() == "true",
                    verify_outcome=row["verify_outcome"],
                    expected_status_contains=row["expected_status_contains"],
                    expected_tool_name=row["expected_tool_name"],
                    coverage_tags=tags,
                )
            )
    return cases


def bar(label: str, count: int, total: int) -> str:
    width = 30
    filled = int(width * (count / total)) if total else 0
    return f"{label:<12} | {'#'*filled}{'.'*(width-filled)} | {count}/{total}"


async def run_case(case: Case) -> Dict[str, Any]:
    registry = FakeRegistry()
    if case.expected_tool_name:
        registry.set_behavior(case.expected_tool_name, case.tool_behavior)

    provider = Phase10MockProvider(case, registry)
    conv_repo = FakeConversationRepo()
    mem_repo = FakeMemoryRepo()
    orch = InstrumentedOrchestrator(provider, registry, conv_repo, mem_repo)
    # Disable embedding/semantic retrieval for eval determinism
    class _StubEmbed:
        async def generate_embedding(self, text):
            return None
    orch.embedding_service = _StubEmbed()

    history = [ChatMessage(role=MessageRole.USER, content=case.query)]

    statuses = []
    tool_calls = []
    final_content = ""

    async for chunk in orch.run(
        conversation_id=str(uuid.uuid4()),
        user_input=case.query,
        conversation_history=history,
        model="mock-model",
        user_id=str(uuid.uuid4()),
    ):
        if chunk.status:
            statuses.append(chunk.status)
        if chunk.tool_calls:
            tool_calls.extend(chunk.tool_calls)
        if chunk.content:
            final_content += chunk.content

    executed_tools = [
        m.get("metadata", {}).get("tool_name")
        for m in conv_repo.messages
        if m.get("role") == MessageRole.TOOL
    ]
    executed_tools = [name for name in executed_tools if name]

    # verification flags from tool message metadata
    verification_flags = [
        m.get("metadata", {}).get("verification_ok")
        for m in conv_repo.messages
        if m.get("role") == MessageRole.TOOL
    ]
    verification_triggered = any(flag is not None for flag in verification_flags)

    result = {
        "test_id": case.test_id,
        "query": case.query,
        "expected_phase": case.expected_phase,
        "actual_phase": getattr(orch, "last_router_decision", {}).get("phase"),
        "expected_tool_required": case.expected_tool_required,
        "actual_tool_required": getattr(orch, "last_router_decision", {}).get("tool_required"),
        "expected_domains": case.expected_domains,
        "actual_domains": getattr(orch, "last_router_decision", {}).get("tool_domains"),
        "executed_tools": executed_tools,
        "tool_calls_detected": [tc.function.name for tc in tool_calls],
        "status_messages": statuses,
        "verification_triggered": verification_triggered,
        "final_content": final_content[:200],
        "coverage_tags": case.coverage_tags,
    }

    if not result["actual_phase"]:
        result["actual_phase"] = "GENERAL"
        result["actual_tool_required"] = False
        result["actual_domains"] = []

    checks = []
    checks.append(result["actual_phase"] == case.expected_phase)
    checks.append(result["actual_tool_required"] == case.expected_tool_required)

    if case.expected_domains:
        checks.append(all(d in (result["actual_domains"] or []) for d in case.expected_domains))

    if case.expected_tool_call_cap and case.expected_phase == "MEDIUM":
        checks.append(len(executed_tools) <= case.expected_tool_call_cap)

    if case.expected_tool_name and (executed_tools or result["tool_calls_detected"]):
        checks.append(case.expected_tool_name in (executed_tools or result["tool_calls_detected"]))

    if case.expected_verification:
        checks.append(verification_triggered)

    if case.expected_status_contains:
        checks.append(any(case.expected_status_contains in s for s in statuses))

    result["passed"] = all(checks)
    result["checks"] = checks

    return result


async def main():
    os.environ["DISABLE_BACKGROUND_EMBEDDING"] = "1"
    cases = parse_cases()
    results = []

    for case in cases:
        results.append(await run_case(case))

    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    phase_counts = {"GENERAL": 0, "MEDIUM": 0, "COMPLEX": 0}
    phase_pass = {"GENERAL": 0, "MEDIUM": 0, "COMPLEX": 0}
    for r in results:
        phase = r["expected_phase"]
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        if r["passed"]:
            phase_pass[phase] = phase_pass.get(phase, 0) + 1

    report = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "results": results,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = ["# Phase 10.0 Orchestrator Eval Report", "", f"Total: {passed}/{total} passed", ""]
    lines.append("## Phase Breakdown")
    for phase in ["GENERAL", "MEDIUM", "COMPLEX"]:
        lines.append(bar(phase, phase_pass.get(phase, 0), phase_counts.get(phase, 0)))
    lines.append("")
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
            lines.append(f"- {r['test_id']}: {r['query']} (actual phase={r['actual_phase']})")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))


if __name__ == "__main__":
    if os.getenv("LIVE_MODE") == "1":
        from tests.evals.run_phase10_orchestrator_live import main as live_main
        asyncio.run(live_main())
    else:
        asyncio.run(main())
