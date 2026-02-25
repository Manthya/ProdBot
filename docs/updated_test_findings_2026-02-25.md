# Updated Test Findings (2026-02-25)

## Scope
- Source review: `README.md` and `docs/*`
- Automated test runs after orchestration and test-suite fixes
- Benchmark trajectory run

## Executive Summary
- Full pytest suite now passes when run with access to localhost services.
- Red-team and benchmark suites remain strong.
- Second-stage optimization for simple directory-list requests is now implemented.
- Remaining instability is concentrated in live tool-heavy runtime behavior (LLM/tool latency), not in current automated suite pass/fail.

## Where It Performs Best

### 1) Full regression suite
- Command: `PYTHONPATH=src .venv/bin/pytest tests -q`
- Result: `17 passed`
- Signal: prior collection/config/test-shape issues are resolved in current branch.

### 2) Adversarial and safety hardening
- Command: `PYTHONPATH=src .venv/bin/pytest tests/redteam -q`
- Result: `10 passed`
- Signal: thread isolation, cycle detection, circuit-breaker behavior remain healthy.

### 3) Benchmark orchestration trajectory
- Command: `PYTHONPATH=src .venv/bin/python tests/evals/run_benchmarks.py`
- Result: `29/29 passed`
- Signal: routing + multi-tool trajectory handling is stable in evaluation harness.

## What Was Improved

### 1) Slow "list files here" path optimization
- Added targeted optimization in orchestrator for simple filesystem listing requests:
  - request detection
  - tool restriction to `directory_tree`
  - safer `directory_tree` args (`path=.` + excludes + optional depth cap)
  - smaller generation budgets for planning and synthesis phases
  - retry fallback with original args if optimized args fail
- File: `src/chatbot_ai_system/orchestrator.py`

### 2) Large tool output guardrails
- Tool output truncation/capping to keep synthesis and persistence stable.
- Files:
  - `src/chatbot_ai_system/orchestrator.py`
  - `src/chatbot_ai_system/services/agentic_engine.py`

### 3) Test reliability fixes
- Async/config and integration tests aligned to current runtime behavior.
- Pytest collection noise reduced via `tests/conftest.py`.
- Files:
  - `tests/test_mcp_config.py`
  - `tests/test_tools_integration.py`
  - `tests/conftest.py`

## Where It Still Fails / Is Noisy

### 1) Runtime warning in red-team flow
- Warning: coroutine not awaited from embed user path in orchestrator.
- Observed in red-team/full test warnings:
  - `src/chatbot_ai_system/orchestrator.py:812`
- Impact: not currently failing tests, but should be cleaned up.

### 2) Environment dependency for full-suite runs
- Full suite needs Redis connectivity; sandboxed local runs may fail with connection permission issues.
- Outside sandbox / proper local infra, suite passes.

### 3) Non-blocking deprecation warnings
- Pydantic/FastAPI/on_event and datetime deprecation warnings are still present.
- Not currently test-failing, but worth backlog cleanup.

## Bottom Line
- Best now: end-to-end automated verification (`17/17`), red-team (`10/10`), benchmarks (`29/29`), and optimized handling for simple directory listing intent.
- Remaining work: clear runtime async warning and further validate live latency under real LLM load for tool-heavy prompts.
