# Failed-Case Improvement Strategy (2026-02-25)

## Goal
Improve reliability for current unstable areas after latest fixes:
- Tool-heavy live latency/timeout risk under real LLM load
- Async warning in background embedding flow
- Embedding service availability warnings in benchmark/local runs
- Deprecation warning backlog

## 1) Tool-Heavy Prompt Latency (`List files...`)

### Observed issue
- Second-stage guardrails are in place (tool restriction + constrained args + token budget caps + output truncation).
- Remaining risk is backend/LLM latency variance in live runs, especially with large repos.

### Strategy A: Add direct no-LLM rendering path for simple directory listings
- Change: if request is identified as simple listing, return formatted tool output directly (or with template-based summary) and skip synthesis LLM round-trip.

Pros:
- Largest latency reduction and fewer timeout points.
- Predictable output and lower token cost.

Cons:
- Less natural language flexibility unless templated well.
- Requires precise intent gating to avoid over-triggering.

### Strategy B: Adaptive depth escalation
- Change: keep depth=2 default; if user asks for recursive/deep detail, progressively increase depth in controlled steps.

Pros:
- Balances speed for common case with accuracy for advanced case.
- Keeps payload growth bounded by explicit escalation.

Cons:
- More branching logic in orchestrator.
- Needs clear user-facing messaging when output is intentionally limited.

### Strategy C: Provider timeout + retry shaping
- Change:
  - one short retry for synthesis with reduced max tokens
  - optional fallback to "tool output only" response when synthesis still times out

Pros:
- Better user experience than hard error/empty output.
- Works well with existing truncation guardrails.

Cons:
- More state branches and complexity.
- Can increase tail latency when retries trigger.

## 2) Async Warning in Background Embed Path

### Observed issue
- Warning in tests: coroutine not awaited around background user embedding path (`_safe_embed_user` -> `_embed_user_message`).

### Strategy A: Normalize async mock handling in tests
- Change: fix offending tests/mocks so sync-like methods do not return un-awaited coroutine objects.

Pros:
- Fastest way to remove warning noise.
- Keeps production path unchanged.

Cons:
- Could hide a real production async contract issue if mocks diverge too much.

### Strategy B: Defensive awaitable handling in orchestrator helper
- Change: detect awaitable from `scalar_one_or_none()` path and await it when needed.

Pros:
- More robust across mock/runtime variations.
- Makes helper resilient to async driver differences.

Cons:
- Slightly more complexity in hot code.
- Needs careful typing/comments.

## 3) Embedding Availability Warnings

### Observed issue
- Benchmark run logs repeated `Error generating embedding: All connection attempts failed`.
- Evaluations still pass, but logs are noisy and can obscure real errors.

### Strategy A: Feature flag to disable embeddings in eval mode
- Change: environment flag (e.g. `DISABLE_BACKGROUND_EMBEDDING=true`) used in eval/local CI.

Pros:
- Cleaner logs and faster runs.
- Deterministic test/eval behavior.

Cons:
- Eval path diverges slightly from production behavior.

### Strategy B: Circuit breaker for embedding failures
- Change: after N failures, pause embedding attempts for cooldown interval.

Pros:
- Reduces repeated noise and wasted retries.
- Keeps production behavior enabled when service recovers.

Cons:
- Additional state management.

## 4) Deprecation Cleanup Backlog

### Observed issue
- Pydantic/FastAPI datetime and lifecycle warnings persist across test runs.

### Strategy
- Incremental cleanup:
  - migrate `Field(..., env=...)` usage to Pydantic v2 `SettingsConfigDict` style
  - replace `on_event` startup/shutdown with lifespan handler
  - replace `datetime.utcnow()` calls with timezone-aware UTC APIs

## Suggested Success Criteria
- Simple directory-list prompt completes under SLA in repeated live runs (for example, <=20s in 5/5 runs).
- No async runtime warnings in `pytest tests -q`.
- Benchmarks run without repeated embedding-connection error spam.
- Current `pytest tests -q`, redteam, and benchmark commands remain green after cleanup changes.
