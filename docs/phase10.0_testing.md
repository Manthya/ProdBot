# Phase 10.0 — Orchestrator & Routing Testing Plan

> **Status:** Not executed yet  
> **Created:** 2026-03-02  
> **Target Model:** Qwen 2.5:14B-Instruct (Ollama, local)  
> **Scope:** Routing, tool selection, tool execution safety, verification, reliability ranking

---

## 1. Test Scope

**Core behaviors under test**
- Deterministic pre-router for explicit tool requests
- JSON router output parsing and fallback behavior
- Phase-based system prompt switching (GENERAL / MEDIUM / COMPLEX)
- MEDIUM phase single-tool cap
- Selective tool verification (errors, empty, large outputs, low confidence, high-risk tools)
- Tool reliability ranking and persistence
- Forced tool-call retry user feedback (no UI freeze)

**Out of scope**
- Frontend UI (Playwright not included)
- Model quality comparisons across providers

---

## 2. Test Matrix (Primary)

| ID | Scenario | Expected Result |
|----|----------|-----------------|
| R-01 | “What time is it right now?” | Pre-router fires TIME; tool_required=true; 1 tool call |
| R-02 | “Tell me a story about time” | No TIME tool; GENERAL response |
| R-03 | “git status” | Pre-router fires GIT; tool_required=true |
| R-04 | “Open https://example.com” | Pre-router fires FETCH; tool_required=true |
| R-05 | “List files in this folder” | FILESYSTEM tools selected; MEDIUM phase |
| R-06 | “Compare file A and file B” | COMPLEX phase; agentic plan |
| R-07 | Router JSON malformed | Fallback parse succeeds or low-confidence fallback |
| R-08 | Router confidence low | GENERAL fallback + clarification |
| R-09 | MEDIUM phase tool spam (LLM emits 3 tool calls) | Orchestrator caps to 1 tool |
| R-10 | Tool output empty | Verification triggers; clarification response |
| R-11 | Tool output > 5000 chars | Verification triggers |
| R-12 | High-risk tool (write/delete) | Verification triggers |
| R-13 | Forced retry path | Emits “Rethinking tool selection…” status |
| R-14 | Tool reliability updated | EMA updates and tool ranking changes |

---

## 3. Automated Tests

### 3.1 Unit & Integration (Pytest)
**Command**
```bash
PYTHONPATH=src ./.venv/bin/pytest tests/test_tools_integration.py -q
PYTHONPATH=src ./.venv/bin/pytest tests/test_redis.py -q
PYTHONPATH=src ./.venv/bin/pytest tests/test_mcp_integration.py -q
```

**Expected**
- All tests pass
- Redis connectivity is stable
- Tool registry returns tools as expected

### 3.2 Redteam (Routing + Tool Safety)
**Command**
```bash
PYTHONPATH=src ./.venv/bin/pytest tests/redteam -q
```

**Expected**
- Router JSON prompt handling uses new mock response
- No deadlocks or tool leakage

### 3.3 Evals (Trajectory + Tool Routing)
**Command**
```bash
PYTHONPATH=src ./.venv/bin/python tests/evals/run_benchmarks.py
```

**Expected**
- Router JSON mock returns valid decision
- No hard errors in routing

### 3.4 Phase 10 Mock Eval (Full Matrix)
**Command**
```bash
python tests/evals/generate_phase10_cases.py
PYTHONPATH=src ./.venv/bin/python tests/evals/run_phase10_orchestrator_eval.py
```

**Expected**
- 100% pass on mock suite (116 cases)
- Tag breakdown rendered in `tests/evals/phase10_report.md`

### 3.5 Phase 10 Live Eval (End-to-End)
**Command**
```bash
PYTHONPATH=src ./.venv/bin/python tests/evals/run_phase10_orchestrator_live.py
```

**Notes**
- Requires backend running on `ws://localhost:8000/api/chat/stream`
- By default skips `side_effect` and `mock_only` cases
- Use `PHASE10_SKIP_TAGS` to override

---

## 4. Manual Test Scenarios

### 4.1 Routing Accuracy
- Prompt: “What time is it in UTC?”
- Expect: TIME tool, MEDIUM phase

- Prompt: “Explain what time is in physics”
- Expect: GENERAL phase, no tool call

### 4.2 MEDIUM Phase Tool Cap
- Prompt: “List files here and show me git status”
- Expect: MEDIUM phase; tool call cap enforces 1 tool only, follow-up needed

### 4.3 Verification Triggers
- Prompt: “Read a large file and summarize”
- Expect: Tool output capped; verification triggers due to size

### 4.4 Forced Retry UX
- Prompt: Tool-required query where model fails to emit tool call
- Expect: Stream status “Rethinking tool selection…” before blocking retry

---

## 5. Performance Checks

**Goal:** Keep TTFT stable for MEDIUM.

**Manual timing**
- Measure TTFT for a MEDIUM query before and after Phase 10.0
- Confirm no extra pre-call in MEDIUM (router adds one classify call; should remain within acceptable local latency)

---

## 6. Expected Risks & Monitoring

**Risk:** Router JSON parse failure on local models  
**Mitigation:** Strict retry + low-confidence fallback

**Risk:** Redis unavailability  
**Mitigation:** ToolReliabilityStore falls back to in-memory stats

**Risk:** Tool call explosion  
**Mitigation:** Hard cap in MEDIUM / expected_tool_calls

---

## 7. Pass/Fail Criteria

**Pass**
- Router consistently selects correct phase on test matrix
- MEDIUM tool cap never executes more than 1 tool
- High-risk outputs trigger verification
- Forced retry yields status chunk
- No crashes in eval and redteam suites

**Fail**
- Tool execution without required verification on high-risk or large payloads
- MEDIUM phase executes multiple tools
- Router parse failure causes crashes or tool hallucination

---

## 8. Post-Run Report Template

**Date:** YYYY-MM-DD  
**Model:** qwen2.5:14b-instruct  
**Routing Accuracy:** X/Y  
**Tool Cap Violations:** 0  
**Verification Triggers:** Count  
**Forced Retry Events:** Count  
**Notable Failures:** (list)
