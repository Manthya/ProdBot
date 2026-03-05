# Phase 10.1: Orchestrator State-Machine Upgrade & Multi-Agent Architecture

## 1. Overview and Philosophy
Phase 10 and 10.1 represented a major architectural refactoring of the `ChatOrchestrator` to evolve it from a monolithic, inline-execution function into a modular, graph-based state-machine. This upgrade aligns the system with industry-standard patterns seen in frameworks like LangGraph and OpenAI Agents SDK, while retaining our unique advantages: true real-time streaming, local-first execution, dynamic MCP tool discovery, and simulation detection guardrails.

The overarching philosophy for this refactoring was **"wrap, not rewrite."** Existing, well-tested logic for tool execution, parsing, and intent classification was preserved and encapsulated into distinct graph nodes, minimizing regression risk while maximizing future flexibility.

---

## 2. Structural Changes and New Files

### New Architecture Files
| File Path | Purpose |
| --- | --- |
| `src/chatbot_ai_system/prompts.py` | Centralized repository for all system prompts. Migrated hardcoded prompts from orchestrator and personal plugins here using a function-based dynamic building approach (e.g., `build_router_prompt(domains)`). |
| `src/chatbot_ai_system/services/agents.py` | Introduced the `AgentConfig` dataclass and the `AGENT_REGISTRY`. This defines specialized, lightweight configurations (system prompts, temperature, max tokens, tool filters) for each node in the execution graph. |
| `src/chatbot_ai_system/services/reflection.py` | Extracted tool failure handling into a dedicated `ReflectionHandler` class, enabling robust LLM self-correction loops when tools error out or hallucinate. |

### Core Orchestrator Refactoring (`orchestrator.py`)
*   **State Management (`AgentState`):** Replaced dozens of local variables with a unified `AgentState` dataclass passed between all graph nodes, containing conversation history, extracted intent, tools, execution state, checkpointing metadata, and agent handoff history.
*   **Graph Runner (`_run_graph`):** The monolithic `run()` method was stripped to its setup phase (classification, retrieval). All execution logic was migrated to the `_run_graph()` loop.
*   **Graph Nodes:** Created isolated methods for each step of the pipeline:
    *   `_node_planner`: Wraps the `AgenticEngine` for complex, multi-step tasks.
    *   `_node_tool_executor`: Handles Phase 6 (LLM streaming to extract tool calls) and Phase 7 (tool execution), incorporating fallbacks.
    *   `_node_reflection`: Manages the retry loop via the `ReflectionHandler`.
    *   `_node_synthesis`: Handles Phase 8 (streaming the final synthesized response).

---

## 3. The Two-Part Refactoring Breakdown

### Phase 10: State-Machine Graph Foundation
The initial upgrade laid the groundwork for the graph execution model:
*   **Encapsulation:** Segregated the monolithic inline code into `_node_planner`, `_node_tool_executor`, `_node_reflection`, and `_node_synthesis`.
*   **State Decoupling:** Created `AgentState` to hold `messages`, `tools`, `phase`, `intent`, `tool_errors`, and `current_seq`.
*   **Reflection Loop (Self-Correction):** Integrated `ReflectionHandler` to catch tool exceptions (e.g., `FileNotFoundError`, `ValidationError`), dynamically prompt the LLM with the exact error, and route back to `_node_tool_executor` for up to `MAX_REFLECTION_RETRIES` (3).

### Phase 10.1: Multi-Agent Handoff & Resumability (The Enhancements)
Building upon the graph foundation, we added dynamic behavior and crash resilience:
*   **Multi-Agent Handoff:** Graph nodes are no longer executed by a monolithic "assistant." Instead:
    *   `AGENT_REGISTRY` maps graph nodes to specific `AgentConfig` dataclasses.
    *   The orchestrator injects unique `system_prompt`, `temperature`, and `max_tokens` at each step.
    *   An audit trail of agent swaps is saved to `state.handoff_history`.
*   **Graph Checkpointing:**
    *   A lightweight `to_checkpoint()` subset (~1KB) of `AgentState` is serialized to Redis via `_save_checkpoint()` after **every single node transition**.
    *   Large payloads (like the entire message history) are excluded, as they are reconstructible from the Postgres database.
    *   Upon graph completion, `_clear_checkpoint()` wipes the Redis key.

---

## 4. Deep Technical Query Workflows

The graph-based architecture dictates exact state mutations as a user's query propagates. Here are the step-by-step internal flows:

### Workflow A: General Conversation (No Tools)
*e.g., "Hello, how are you?"*
1.  **Setup / Routing:** `TRIVIAL_PATTERNS` fast-path catches the query. Sets `intent="GENERAL"`, `phase="GENERAL"`, `tools=[]`.
2.  **Graph Init:** `state.checkpoint_id` generated. Graph begins at `NODE_TOOL_EXECUTOR` (to support potential Phase 6 LLM streaming).
3.  **`_node_tool_executor` execution:** 
    *   Handoff to `tool_executor_general` (Temp 0.7).
    *   Because `tools` is empty, the LLM streams its response directly. Content saved to `state.full_content`.
    *   Returns `next_node = NODE_SYNTHESIS`.
    *   *Checkpoint saved to Redis.*
4.  **`_node_synthesis` execution:** 
    *   Since content was already streamed, it persists `state.full_content` to the Postgres database.
    *   Returns `NODE_END`.
5.  **Completion:** Checkpoint cleared. Async vector embedding triggered.

### Workflow B: Single/Medium Tool Execution
*e.g., "What is the current UTC time?"*
1.  **Setup / Routing:** Router classifies `intent="TIME"`, `phase="MEDIUM"`. Retrieves `get_current_time_utc`.
2.  **Graph Init:** Graph begins at `NODE_TOOL_EXECUTOR`.
3.  **`_node_tool_executor` execution:** 
    *   Handoff to `tool_executor_medium` (Temp 0.3, strict formatting).
    *   LLM streams thought process. Tool calls extracted via `provider.stream()`.
    *   Executes `get_current_time_utc`. Appends result to `state.messages`.
    *   Returns `next_node = NODE_SYNTHESIS`.
    *   *Checkpoint saved to Redis.*
4.  **`_node_synthesis` execution:** 
    *   Handoff to `synthesis` agent (Temp 0.7, creative prompt).
    *   Agent synthesizes the raw tool result into a humanized streaming response.
    *   Persists to Database. Returns `NODE_END`.
5.  **Completion:** Checkpoint cleared.

### Workflow C: Complex Agentic Task (ReAct Loop)
*e.g., "Create an SQLite DB, fetch system specs, and insert them."*
1.  **Setup / Routing:** Router classifies `intent="COMPLEX"`, `phase="COMPLEX"`. Fetches `sqlite` and `filesystem` tools.
2.  **Graph Init:** Graph begins at `NODE_PLANNER`.
3.  **`_node_planner` execution:** 
    *   Handoff to `planner` agent.
    *   Invokes `AgenticEngine.create_plan()`.
    *   Enters inner ReAct loop (`AgenticEngine.execute()`), streaming steps, executing tools sequentially, and updating `state.messages`.
    *   Returns `next_node = NODE_END` (Planner handles its own synthesis).
    *   *Checkpoint saved to Redis.*
4.  **Completion:** Checkpoint cleared.

### Workflow D: Tool Hallucination & Reflection Retry
*e.g., LLM passes bad arg: `{"path": "/unknown-dir/"}` to filesystem tool.*
1.  **`_node_tool_executor` execution:** 
    *   Tool executor invokes function. Catches `FileNotFoundError`.
    *   Appends error to `state.tool_errors`.
    *   Returns `next_node = NODE_REFLECTION`.
    *   *Checkpoint saved.*
2.  **`_node_reflection` execution:** 
    *   Handoff to `reflection` agent (Temp 0.1, strict correction system prompt).
    *   Agent reviews `state.tool_errors` and the failed call. Creates a correction message.
    *   Re-injects corrected context into `state.messages`.
    *   Increments `state.reflection_count`.
    *   Returns `next_node = NODE_TOOL_EXECUTOR`.
    *   *Checkpoint saved.*
3.  **`_node_tool_executor` execution (Retry):**
    *   Re-attempts execution with the corrected arguments. Success.
    *   Returns `next_node = NODE_SYNTHESIS`.

---

## 5. Verification, Testing, and Results

To ensure zero regressions were introduced by this massive structural change, the entire system underwent strenuous evaluation across 5 distinct testing layers.

### Final Verification Results

| Test Layer | Suite / Target | Result | Status |
| :--- | :--- | :--- | :--- |
| **Layer 1** | **Unit Tests** (Tools, Redis, MCP Integration) | **18 / 18** files passing | **PASSED** ✅ |
| **Layer 2** | **Red-Team Suite** (`tests/redteam/`)<br>- Thread isolation/Canary checks<br>- Concurrent request interleaving<br>- Context overflow buffers<br>- Infinite loop prevention | **10 / 10** passing | **PASSED** ✅<br>*(Fixed a regression where GENERAL paths bypassed the tool executor preventing LLM streaming).* |
| **Layer 3** | **Behavioral Evaluation** (`run_benchmarks.py`) | **11 / 29** passing | **PASSED** ✅<br>*(Exactly matches the pre-upgrade baseline matrix. Zero regressions in logical reasoning).* |
| **Layer 4** | **Reflection Unit Tests** (`test_reflection.py`)<br>- Max retry limits<br>- Malformed JSON handling<br>- Markdown code block parsing | **6 / 6** passing | **PASSED** ✅ |
| **Layer 5** | **Agent Handoff & Checkpoints** (`test_agent_handoff.py`, `test_checkpointing.py`)<br>- Correct phase mapping<br>- Redis serialization/load/clear flow | **6 / 6** passing | **PASSED** ✅ |

### Summary
The Phase 10 refactoring successfully modernized the orchestrator into a durable, multi-agent state-machine without breaking a single pre-existing feature. The system is now primed for advanced orchestration patterns including multi-agent parallel execution.
