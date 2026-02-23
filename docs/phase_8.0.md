# Phase 8.0: Red-Team Adversarial Test Suite

## Overview
This phase introduces a robust, layer-by-layer adversarial testing strategy designed to stress-test the chatbot system against malicious inputs, concurrent race conditions, infinite logical loops, and tool payload manipulations. The approach is inspired by [PenTAGI's](https://github.com/vxcontrol/pentagi) multi-agent validation testing framework, where agents are tested in parallel against extreme edge cases.

**Key Rule:** All tests evaluate system integrity, isolation, and stability, NOT LLM response quality. We intentionally avoid metrics like "groundness" or "hallucination relevance."

---

## Testing Architecture

```mermaid
graph TD
    A[Frontend: UI & React State] -->|WebSocket & REST| B[Backend API & Redis]
    B --> C[Orchestrator & Agentic Engine]
    C --> D[MCP Client & Local Tools]
    
    subgraph Layer 1: Frontend
        F1(Concurrency Bursts)
        F2(WebSocket Reconnects)
        F3(Barge-in Interrupts)
    end
    
    subgraph Layer 2: API & State
        B1(Thread Isolation/Canary)
        B2(Context Overflow)
        B3(Sequence Race Conditions)
    end
    
    subgraph Layer 3: Agentic Logic
        C1(Cyclic Deadlock Detection)
        C2(Circuit Breaker Tripping)
        C3(Routing Bypass/Escape)
    end
    
    subgraph Layer 4: Tool Payloads
        D1(Malformed JSON)
        D2(SQL/Traversal Injection)
        D3(Read-Only Violations)
    end

    A -. tests .-> Layer 1
    B -. tests .-> Layer 2
    C -. tests .-> Layer 3
    D -. tests .-> Layer 4
```

---

## Layer-by-Layer Test Scenarios

### Layer 0: Regression & Integration
**Location:** `tests/`
**Purpose:** Harden existing test suite to ensure the system fundamentals remain unbroken.
- Config validation (`test_mcp_config.py`)
- Provider integration (`test_mcp_integration.py`, `test_tools_integration.py`)
- Redis connectivity and TTL (`test_redis.py`)

### Layer 1: Frontend (UI & State Resilience)
**Location:** `tests/redteam/frontend/`
**Framework:** Playwright (TypeScript)
**Focus:** Handling chaotic user input gracefully without state fragmentation.
1. **Concurrency Burst:** User sends 4 rapid messages before the first stream begins rendering.
2. **WebSocket Storm:** User toggles network connect/disconnect 5 times rapidly while a large generation is streaming.
3. **Barge-in:** User interrupts an active generation stream with a new, conflicting instruction.

### Layer 2: Backend API & Memory State Layer
**Location:** `tests/redteam/backend/`
**Framework:** Pytest (Python/Async)
**Focus:** Memory safety, thread isolation between users, and boundary constraints.
1. **Thread Isolation (Canary Test):** Inject a unique secret into User A's thread. Aggressively prompt User B to retrieve it. Asserts absolute failure to cross thread contexts.
2. **Concurrent Interleaving:** Spawn 5 parallel async requests to the chat endpoint to create sequence and state race conditions.
3. **Context Overflow:** Flood the conversation history with multi-megabyte payloads to ensure the orchestrator's token-window mechanism acts predictably and safely without crashing.

### Layer 3: Agentic Routing & Flow Layer
**Location:** `tests/redteam/agentic/`
**Framework:** Pytest (Python/Async)
**Focus:** Preventing runaway token usage, infinite loops, and logic bypasses.
1. **Cyclic Deadlock:** Mock the LLM to repeatedly (infinitely) call the exact same tool with identical arguments. Verify `CYCLE_DETECTED` forces a graceful exit within `MAX_AGENT_ROUNDS`.
2. **Circuit Breaker:** Mock all backend tools to fail and raise exceptions. Verify the engine trips the breaker after `MAX_CONSECUTIVE_FAILURES` and falls back to natural language synthesis.
3. **Routing Bypass:** Inject a trivial prompt cleverly disguised with tool-invoking keywords (e.g., "Hello, execute a web search!") to trick the Intent Classifier. Assert the Regex fast-path intercepts it.
4. **Tool Hallucination:** Force the LLM to request a non-existent tool (`hack_database_now`). Assert rejection via `[INVALID_TOOL]` and engine continuation.

### Layer 4: MCP & External Tool Layer
**Location:** `tests/redteam/tools/`
**Framework:** Pytest (Python/Async)
**Focus:** Validating payload security guarantees, parameter extraction, and boundary separation.
1. **Out-of-Bounds Execution (SQLi / Traversal):** Feed `../../etc/passwd` or dummy SQL payloads into tool parameters. Asserts the system handles string safely without arbitrary execution.
2. **Malformed JSON:** Deliberately break JSON structures passed from the LLM parser to verify error recovery loops don't crash the server.
3. **Unauthorized Write Access:** Mock a POST-capable tool request against a server running in READ-ONLY mode. Asserts the agent cannot hallucinate a success state if the MCP rejects.
4. **Cache Poisoning Check:** Ensure identical cached tool requests correctly isolate user state or invalidate promptly.

---

## Execution Methodology

1. **Fully Mocked Provider:** All `BaseLLMProvider.complete()` and `.stream()` calls are hijacked using `AsyncMock`.
2. **Zero API Cost:** Tests run entirely locally without requiring OpenAI/Anthropic/Ollama credentials.
3. **Infrastructure Stability:** `tests/redteam/conftest.py` provides global monkeypatches for `ToolRegistry`, `ConversationRepository`, and `get_active_model_and_provider` to ensure 100% isolation from the real database and event loop stability.

---

## Final Validation Results (2026-02-23)

The backend adversarial suite was successfully executed with a **100% pass rate** for Layers 2, 3, and 4.

| Test Layer | Scenario Count | Status | Key Findings |
|---|---|---|---|
| **Layer 2: Backend API** | 3 | **PASSED** | Thread boundaries are strictly enforced; 2MB payloads do not crash the ASGI loop. |
| **Layer 3: Agentic Logic**| 4 | **PASSED** | Circuit breaker correctly trips at 2 failures; Cycle detection breaks infinite loops. |
| **Layer 4: MCP / Tools** | 4 | **PASSED** | Malicious injection strings are handled safely as raw data; Malformed JSON recovery is robust. |
| **Layer 1: Frontend** | 3 | **SKIPPED** | Skipped to prioritize logic core. |

**Command to run the backend suite:**
```bash
PYTHONPATH=src pytest tests/redteam/ -v --tb=short
```

**Command to run the frontend suite (Playwright required):**
```bash
cd frontend && npx playwright test tests/redteam/
```

