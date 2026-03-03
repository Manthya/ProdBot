# Phase 10.0 — Orchestrator & Routing Reliability Upgrade

## Summary
Phase 10.0 strengthens routing, tool selection, and safety for local models without changing the LLM provider. The focus is on predictable phase routing (GENERAL / MEDIUM / COMPLEX), safer tool execution, improved tool ranking, and more resilient fallbacks.

## Objectives
- Improve routing accuracy with deterministic and JSON-based routing.
- Enforce MEDIUM-phase “single-step” behavior.
- Add selective verification for risky or low-confidence tool outputs.
- Track tool reliability and use it to rank tools.
- Keep TTFT low by avoiding extra LLM calls for “micro-plans”.

## Changes — Orchestrator (`src/chatbot_ai_system/orchestrator.py`)

### 1) Deterministic Pre-Router (Strict Patterns)
**What:** Added a strict, deterministic pre-router that only fires for explicit tool intent (e.g., time, git, fetch, filesystem, sqlite).  
**Why:** Prevents false positives like “tell me a story about time.”  
**Impact:** More precise routing and fewer accidental tool calls.

### 2) JSON Router with Robust Parsing + Fallback
**What:** Added a router prompt that outputs a JSON decision:
`phase`, `tool_required`, `tool_domains`, `expected_tool_calls`, `confidence`, `need_clarification`.  
Includes strict retry and robust parsing (JSON → regex → key/value fallback).  
**Why:** Local models are inconsistent with schema; this makes routing resilient.  
**Impact:** Better routing stability across local models (qwen2.5, llama3).

### 3) Phase-Based System Prompts
**What:** `_get_system_prompt` now uses `phase`:
- GENERAL: concise, no tool references
- MEDIUM: single-step guidance
- COMPLEX: multi-step + verification guidance  
**Why:** Aligns assistant behavior with routing decisions.  
**Impact:** More consistent tone and flow.

### 4) Tool Domain Routing + Reliability Ranking
**What:** `_filter_tools_for_domains` collects tools across domains, adds query-local candidates, caps size, then ranks by reliability.  
**Why:** Avoids tool overload and prioritizes stable tools.  
**Impact:** Higher tool relevance + fewer failures.

### 5) Tool Reliability Tracking
**What:** New `ToolReliabilityStore` tracks success/failure and EMA scores (Redis + in-memory fallback).  
**Why:** Enables ranking tools based on empirical success.  
**Impact:** Better tool selection over time.

### 6) Selective Verification After Tool Calls
**What:** Verification step runs only when:
- tool error/empty output
- low confidence
- high-risk tool
- large output (>5000 chars)  
**Why:** Full verification on every tool is too slow for local models.  
**Impact:** Safer outputs without doubling latency.

### 7) Clarification Instead of Fail-Closed
**What:** For tool-required requests that fail verification or have no matching tools, the system asks for clarification instead of hard refusing.  
**Why:** ChatGPT-like recovery and smoother UX.  
**Impact:** Fewer dead ends for users.

### 8) MEDIUM Phase Tool Cap
**What:** If `phase == MEDIUM` or `expected_tool_calls == 1`, tool calls are capped to a single tool across:
- streamed tool calls
- fallback JSON parsing
- forced retry path  
**Why:** Enforces single-step behavior even if the model emits multiple tool calls.  
**Impact:** Lower latency and predictable behavior.

### 9) Tool-Required Guard Uses Router Output
**What:** `_requires_tool_execution` now respects `tool_required` from routing instead of relying only on intent heuristics.  
**Why:** Makes enforcement match routing decisions.  
**Impact:** More consistent tool gating.

### 10) TTFT Freeze Mitigation on Forced Retry
**What:** Emit a status chunk before the blocking retry (“Rethinking tool selection…”).  
**Why:** Prevents UI from feeling frozen during forced tool-call retry.  
**Impact:** Better perceived responsiveness.

## Changes — Agentic Engine (`src/chatbot_ai_system/services/agentic_engine.py`)
No functional changes were required in Phase 10.0. The agentic engine continues to handle COMPLEX flows, now triggered via the new phase router. The orchestration logic routes COMPLEX requests into the existing plan + execute flow, preserving prior behavior.

## Test & Mock Updates
- Updated eval mocks to return routing JSON when router prompts are detected.
- Updated redteam mocks to respond to routing prompts.

## Summary of Key Benefits
- More robust routing with fewer false positives.
- Better tool selection via reliability ranking.
- Safer tool use through selective verification.
- Enforced single-step MEDIUM phase behavior.
- Improved UX during retries and failures.
