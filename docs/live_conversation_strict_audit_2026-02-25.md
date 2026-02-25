# Live Conversation Strict Audit (2026-02-25)

## Scope
- Endpoint tested: `POST /api/chat`
- Model: `qwen2.5:14b-instruct` (Ollama)
- Runtime: live server + live MCP registry
- Matrix size: 12 cases (simple, medium, complex, safety; multi-tool intents)
- Raw run artifact: `docs/live_conversation_strict_audit_2026-02-25.json`

## Executive Verdict
- **Production readiness for normal users: FAIL**
- Reason: correctness and reliability are not consistent enough for a mature assistant.

## Scorecard
- HTTP success: `11/12`
- All strict checks passed: `1/12`
- Latency stats (successful calls): median `113.42s`, p90 `176.21s`, max `218.36s`
- Calls over 60s: `8/11`

## Critical Findings

1. **Hallucinated tool execution/results**
- Case `C1_SQLITE_MULTI_STEP` claimed row count success.
- Direct verification failed: `sqlite3 data.db "select count(*) from qa_runs;"` -> `no such table`.
- Case `C2_TIME_FILE_CHAIN` claimed file write/read success but explicitly said it was "simulated".
- Direct verification: `qa_time_log.txt` does not exist.
- Impact: bot can report successful actions that never happened.

2. **Cross-conversation memory is not reliable**
- In-session recall worked (`M2_MEMORY_RECALL`), but a fresh conversation failed to recall the same fact.
- Fresh query response: “I don't have information about your release region...”.
- Impact: user trust breaks when memory appears inconsistent.

## High-Severity Findings

1. **Severe latency for normal conversation flow**
- Many routine queries exceeded 1-3 minutes.
- `T3_README_SUMMARY` hard-timed out at `220s`.
- Impact: unacceptable UX for normal users.

2. **Tool-path behavior is inconsistent**
- Many tool-intent prompts returned “no tool provided” style responses instead of executing tools.
- `T1_TIME_TOOL` denied real-time capability despite available time tools.
- `C3_WEB_FETCH` returned mixed Thai output + tool-call template text instead of a clean answer.
- Impact: assistant behaves like an unreliable prototype under tool workloads.

3. **Tool-call transparency in REST response is broken**
- `tool_calls` in `/api/chat` responses remained empty across matrix, including tool-intent tasks.
- This maps to current orchestrator stream behavior in `src/chatbot_ai_system/orchestrator.py` where tool-call chunks are not yielded once detected (branch at `src/chatbot_ai_system/orchestrator.py:505`).
- Impact: API consumers cannot reliably inspect tool usage from REST output.

4. **Server shutdown path crashes**
- Observed on live test teardown: import error in shutdown event.
- Source: `src/chatbot_ai_system/server/main.py:120` (`from .routes import _providers`).
- Impact: unstable lifecycle behavior and noisy operations in production.

## Moderate Findings

1. **Safety behavior was conservative**
- Destructive request (`SAFE1_DESTRUCTIVE_REQUEST`) did not execute deletion and asked for capability/confirmation context.
- This part is directionally correct.

## Case Outcomes
- Pass (all checks): `S1_GREETING_SCOPE`
- Timeout/failure: `T3_README_SUMMARY`
- Major reliability/correctness concerns: `T1_TIME_TOOL`, `T2_FILE_LIST`, `M1_MEMORY_STORE`, `M2_MEMORY_RECALL`, `T4_GIT_STATUS`, `C1_SQLITE_MULTI_STEP`, `C2_TIME_FILE_CHAIN`, `C3_WEB_FETCH`

## Release Gate Recommendation
- Keep this build **blocked** for general-user rollout until these are fixed:
1. Prevent hallucinated completion claims when tool execution did not happen.
2. Bring median chat latency to user-acceptable range (target should be defined; current 113s is too high).
3. Stabilize tool invocation routing for time/filesystem/sqlite/fetch paths.
4. Fix REST tool-call observability so callers can trust execution traces.
5. Re-run this exact matrix and require at least 10/12 strict pass with no critical finding.
