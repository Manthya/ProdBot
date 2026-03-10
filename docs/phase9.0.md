# Phase 9.0 — Personal Assistant Integration (Post‑Phase 8.1)

## Executive Summary
After Phase 8.1, we implemented the first iteration of **Personal Assistant Mode** focused on Phase 1 platforms: **Gmail, Telegram, and LinkedIn**. This phase adds:
- A **Personal Assistant** section in the Plugins dashboard.
- **Local-first MCP integrations** for Gmail/Telegram/LinkedIn with feature flags.
- A **Human‑In‑The‑Loop (HITL)** flow for message sending via Draft Cards.
- Backend endpoints to store platform configuration, drive connect flows, and execute user‑approved sends.

This phase is designed to be **safe**, **local**, and **extensible**. It does not auto-send messages; it gates send actions behind explicit user confirmation.

---

## What We Implemented (Detailed)

### 1) Personal Assistant Feature Flags (Env‑Only)
We introduced three environment flags that control whether Personal Assistant integrations are active:
- `ENABLE_PERSONAL_GMAIL`
- `ENABLE_PERSONAL_TELEGRAM`
- `ENABLE_PERSONAL_LINKEDIN`

These are **env‑only** and read at runtime. If a flag is `false`, the platform is considered disabled and the UI hides it.

### 2) MCP Server Wiring for Phase 1 Platforms
We integrated MCP server definitions for Gmail, Telegram, and LinkedIn (Playwright MCP) directly into the MCP server config loader:
- **Gmail**: `npx -y @shinzolabs/gmail-mcp`
- **Telegram**: `npx -y @chaindead/telegram-mcp`
  - Requires `TG_APP_ID` and `TG_API_HASH` (if missing, server is skipped)
- **LinkedIn**: `npx -y @playwright/mcp@latest --isolated`

The MCP servers are loaded via the existing `get_mcp_servers()` pipeline and will appear in `/api/plugins/status` when enabled.

**Why MCP?**
- MCP keeps credentials local and lets us use tool‑calling instead of custom API plumbing.
- MCP also keeps the LLM interface consistent (tools = functions).

### 3) Personal Integration Configuration Storage
We store Personal Assistant configuration under a single dynamic setting:
```
personal_integrations
```

This is a JSON object in the `system_settings` table (DB), managed by `SettingsManager`.

Per‑platform config looks like:
```json
{
  "gmail": {
    "fields": {
      "MCP_CONFIG_DIR": "~/.gmail-mcp",
      "AUTH_SERVER_PORT": "3000"
    },
    "permissions": {
      "read": true,
      "draft": true,
      "send": false
    }
  }
}
```

**Why store in DB?**
- Keeps config persistent across restarts.
- Allows the UI to render saved state without additional secrets tooling.

### 4) Personal Assistant API Routes
We added a dedicated router at:
```
/api/personal
```

Endpoints:
- `GET /api/personal/status`
  - Returns enabled flags, schemas, saved configs, and configured status.
- `POST /api/personal/integrations`
  - Save platform fields and permissions.
- `POST /api/personal/connect`
  - Save fields and return the **exact CLI command** to run for OAuth/login.
- `POST /api/personal/send`
  - Execute a tool call **only after user approval**.

**Important behavior:**
- `/send` enforces permissions before running the tool.
- `/connect` registers MCP client immediately and refreshes tools.

### 5) Personal Assistant UI (Plugins Dashboard)
We added a new section to `PluginsDashboard.tsx`:
- Cards for Gmail, Telegram, LinkedIn.
- A detailed view with tabs:
  - **Overview** — status summary.
  - **Connect** — fields and save/connect flow.
  - **Permissions** — toggles for `read`, `draft`, `send`.

The Connect tab renders fields from backend schemas (so it’s backend‑driven).

### 6) Draft Cards + HITL Send Flow
We implemented Draft Cards in the chat UI for specific tools:
- `gmail_draft`
- `telegram_send`
- `linkedin_send`
- `linkedin_send_message`

Flow:
1. LLM emits a tool call.
2. If the tool is a **HITL tool**, the orchestrator does **not execute** it.
3. The UI renders a **Draft Card** with editable fields.
4. User clicks **Send Now**.
5. Frontend calls `POST /api/personal/send`.
6. Backend runs the tool and stores a success message.

**Why this design?**
- Prevents accidental sends.
- Keeps final approval human‑controlled.
- Tool call payload is editable before execution.

### 7) Orchestrator Safety (HITL Gating)
We added a small guard in the orchestration step:
- When tool calls include HITL tools, the system:
  - **Persists** the tool call to DB,
  - Emits a status like “Awaiting your confirmation…”,
  - **Stops** execution until the user approves.

This avoids tool calls being executed automatically by the LLM.

---

## How It Works End‑to‑End (Flow)

1. **User requests an action** (e.g., “Draft a message to John on Telegram”).
2. LLM generates a **tool call** (e.g., `telegram_send`).
3. Orchestrator detects that tool is HITL → **does not execute**.
4. Chat UI shows **Draft Card** with editable fields.
5. User clicks **Send Now**.
6. Frontend calls `/api/personal/send` with the final payload.
7. Backend validates permissions and runs tool.
8. Tool output is saved to the conversation.

---

## Tech Stack Used (and Why)

### Backend
- **FastAPI** — existing API layer, consistent with prior phases.
- **MCP (Model Context Protocol)** — tool standardization and local execution.
- **SQLAlchemy + Postgres** — persistent config and conversation storage.
- **Redis** — existing caching layer for tool results.

### Frontend
- **Next.js (App Router)** — existing UI framework.
- **React** — Draft Cards and Plugins UI.
- **Lucide Icons** — consistent UI styling.

### MCP Server Choices
- **Gmail MCP**: `@shinzolabs/gmail-mcp` (OAuth + native Gmail API)
- **Telegram MCP**: `@chaindead/telegram-mcp` (MTProto client)
- **LinkedIn MCP**: `@playwright/mcp@latest` (browser automation)

**Why these?**
- All run locally and avoid cloud credentials storage.
- Provide standardized tool definitions.
- Each has active community adoption.

---

## Running Locally (README‑Aligned)

**Containers (only data services):**
```bash
docker compose up -d postgres redis
```

**Backend (local):**
```bash
export DATABASE_URL=postgresql+asyncpg://chatbot:password@localhost:5432/chatbot
export POSTGRES_URL=postgresql+asyncpg://chatbot:password@localhost:5432/chatbot
PYTHONPATH=./src ./.venv/bin/uvicorn chatbot_ai_system.server.main:app --reload --host 0.0.0.0 --port 8000
```

**Frontend (local):**
```bash
cd frontend
npm run dev
```

---

## Known Issues / Notes

1. **`.env` sourcing in zsh**  
`CORS_ORIGINS=["..."]` is parsed as a zsh array, which breaks startup.  
Workaround: pass env vars explicitly or quote the value.

2. **Database migrations**  
If DB already has tables, `alembic upgrade head` may error with `DuplicateTable`.  
This is expected when DB already initialized.

3. **Telegram MCP requires API credentials**  
If `TG_APP_ID` and `TG_API_HASH` aren’t provided, the Telegram MCP server is skipped.

4. **LinkedIn MCP requires browser session**  
You must create storage state or use a user data dir before use.

---

## Summary of New Files/Changes

**New**
- `src/chatbot_ai_system/server/personal_routes.py`
- `src/chatbot_ai_system/personal/constants.py`

**Updated**
- `src/chatbot_ai_system/config/mcp_server_config.py`
- `src/chatbot_ai_system/config/settings_manager.py`
- `src/chatbot_ai_system/server/main.py`
- `src/chatbot_ai_system/orchestrator.py`
- `src/chatbot_ai_system/services/agentic_engine.py`
- `frontend/components/PluginsDashboard.tsx`
- `frontend/components/ChatArea.tsx`
- `frontend/app/page.tsx`
- `pyproject.toml`
- `.env`

---

## Why This Phase Matters
Phase 9.0 is the **foundation** for safe personal integrations:
- It respects **user privacy** (local execution).
- It enforces **human confirmation** for sending.
- It adds **configurable permissions** and a UI control plane.
- It is extensible to Phase 2+ platforms with minimal changes.

This makes the system ready for safe, real‑world personal assistant tasks without sacrificing control or privacy.
