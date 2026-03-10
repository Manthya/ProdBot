import logging
import os
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot_ai_system.config.mcp_server_config import get_mcp_servers
from chatbot_ai_system.config.settings_manager import settings_manager
from chatbot_ai_system.personal.constants import PERSONAL_PLATFORM_SCHEMAS
from chatbot_ai_system.repositories.conversation import ConversationRepository
from chatbot_ai_system.database.session import get_db
from chatbot_ai_system.tools import registry
from chatbot_ai_system.tools.mcp_client import MCPClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/personal", tags=["personal"])


class PersonalIntegrationUpdate(BaseModel):
    platform: str = Field(..., description="gmail | telegram | linkedin")
    fields: Optional[Dict[str, Any]] = None
    permissions: Optional[Dict[str, bool]] = None


class PersonalConnectRequest(BaseModel):
    platform: str = Field(..., description="gmail | telegram | linkedin")
    fields: Dict[str, Any] = Field(default_factory=dict)


class PersonalSendRequest(BaseModel):
    conversation_id: str
    tool_name: str
    arguments: Dict[str, Any]


def _enabled_flags() -> Dict[str, bool]:
    return {
        "gmail": os.environ.get("ENABLE_PERSONAL_GMAIL", "false").lower() == "true",
        "telegram": os.environ.get("ENABLE_PERSONAL_TELEGRAM", "false").lower() == "true",
        "linkedin": os.environ.get("ENABLE_PERSONAL_LINKEDIN", "false").lower() == "true",
    }


def _normalize_platform(name: str) -> str:
    return name.strip().lower()


def _merge_personal_integrations(
    existing: Dict[str, Any], platform: str, fields: Optional[Dict[str, Any]], permissions: Optional[Dict[str, bool]]
) -> Dict[str, Any]:
    merged = dict(existing or {})
    platform_cfg = dict(merged.get(platform) or {})
    if fields:
        platform_cfg["fields"] = {**(platform_cfg.get("fields") or {}), **fields}
    if permissions:
        platform_cfg["permissions"] = {**(platform_cfg.get("permissions") or {}), **permissions}
    merged[platform] = platform_cfg
    return merged


def _platform_configured(platform: str, fields: Dict[str, Any]) -> bool:
    schema = PERSONAL_PLATFORM_SCHEMAS.get(platform, {})
    required = [f["key"] for f in schema.get("fields", []) if f.get("required") == "true"]
    if platform == "linkedin":
        return bool(fields.get("USER_DATA_DIR") or fields.get("STORAGE_STATE_PATH"))
    return all(str(fields.get(k, "")).strip() for k in required)


def _build_connect_command(platform: str, fields: Dict[str, Any]) -> str:
    if platform == "gmail":
        mcp_dir = fields.get("MCP_CONFIG_DIR", "~/.gmail-mcp")
        port = str(fields.get("AUTH_SERVER_PORT", "3000"))
        return (
            f"MCP_CONFIG_DIR={mcp_dir} AUTH_SERVER_PORT={port} "
            "npx @shinzolabs/gmail-mcp auth\n"
            f"OAuth keys file: {mcp_dir.rstrip('/')}/gcp-oauth.keys.json"
        )
    if platform == "telegram":
        return "npx -y @chaindead/telegram-mcp auth"
    if platform == "linkedin":
        storage_state = fields.get("STORAGE_STATE_PATH", "")
        user_data_dir = fields.get("USER_DATA_DIR", "")
        args = ["npx", "@playwright/mcp@latest"]
        if storage_state:
            args.extend(["--storage-state", storage_state])
        if user_data_dir:
            args.extend(["--user-data-dir", user_data_dir])
        args.append("--isolated")
        return " ".join(args)
    return ""


def _platform_from_tool(tool_name: str) -> Optional[str]:
    if tool_name.startswith("gmail_"):
        return "gmail"
    if tool_name.startswith("telegram_"):
        return "telegram"
    if tool_name.startswith("linkedin_"):
        return "linkedin"
    return None


def _permission_from_tool(tool_name: str) -> str:
    lowered = tool_name.lower()
    if "draft" in lowered:
        return "draft"
    if "send" in lowered:
        return "send"
    return "read"


@router.get("/status")
async def personal_status():
    integrations = await settings_manager.get_setting("personal_integrations") or {}
    enabled = _enabled_flags()
    status = {}
    for platform in ["gmail", "telegram", "linkedin"]:
        fields = (integrations.get(platform) or {}).get("fields") or {}
        status[platform] = {
            "enabled": enabled.get(platform, False),
            "configured": _platform_configured(platform, fields) if enabled.get(platform) else False,
        }

    return {
        "enabled": enabled,
        "integrations": integrations,
        "schemas": PERSONAL_PLATFORM_SCHEMAS,
        "status": status,
    }


@router.post("/integrations")
async def update_personal_integrations(update: PersonalIntegrationUpdate):
    platform = _normalize_platform(update.platform)
    if platform not in PERSONAL_PLATFORM_SCHEMAS:
        raise HTTPException(status_code=400, detail="Unsupported platform")
    if not _enabled_flags().get(platform, False):
        raise HTTPException(status_code=403, detail="Platform is disabled")

    current = await settings_manager.get_setting("personal_integrations") or {}
    updated = _merge_personal_integrations(current, platform, update.fields, update.permissions)
    await settings_manager.set_setting("personal_integrations", updated, "Personal assistant integrations")

    return {"status": "updated", "integrations": updated}


@router.post("/connect")
async def connect_personal_integration(request: PersonalConnectRequest):
    platform = _normalize_platform(request.platform)
    if platform not in PERSONAL_PLATFORM_SCHEMAS:
        raise HTTPException(status_code=400, detail="Unsupported platform")
    if not _enabled_flags().get(platform, False):
        raise HTTPException(status_code=403, detail="Platform is disabled")

    current = await settings_manager.get_setting("personal_integrations") or {}
    updated = _merge_personal_integrations(current, platform, request.fields, None)
    await settings_manager.set_setting("personal_integrations", updated, "Personal assistant integrations")

    # Register MCP client immediately so tools appear after connect
    try:
        servers = await get_mcp_servers()
        match = next((s for s in servers if s.name == platform), None)
        if match:
            existing = [c.name for c in registry._mcp_clients]
            if match.name not in existing:
                client = MCPClient(
                    name=match.name,
                    command=match.command,
                    args=match.args,
                    env=match.env_vars or os.environ.copy(),
                )
                registry.register_mcp_client(client)
            await registry.refresh_remote_tools()
    except Exception as e:
        logger.warning(f"Personal integration connect warning: {e}")

    return {
        "status": "saved",
        "next_step_command": _build_connect_command(platform, request.fields),
        "configured": _platform_configured(platform, request.fields),
    }


@router.post("/send")
async def send_personal_action(request: PersonalSendRequest, db: AsyncSession = Depends(get_db)):
    try:
        tool = registry.get_tool(request.tool_name)
    except Exception:
        raise HTTPException(status_code=400, detail="Tool not found")

    platform = _platform_from_tool(request.tool_name)
    if platform:
        integrations = await settings_manager.get_setting("personal_integrations") or {}
        permissions = (integrations.get(platform) or {}).get("permissions") or {}
        required_perm = _permission_from_tool(request.tool_name)
        if not permissions.get(required_perm, False):
            raise HTTPException(status_code=403, detail="Permission denied for this action")

    try:
        result = await tool.run(**request.arguments)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {e}")

    # Persist tool message and assistant acknowledgement
    conv_repo = ConversationRepository(db)
    conv_id = UUID(request.conversation_id)
    conversation = await conv_repo.get(conv_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    current_seq = await conv_repo.get_next_sequence_number(conv_id)

    await conv_repo.add_message(
        conversation_id=conv_id,
        role="tool",
        content=str(result),
        sequence_number=current_seq,
    )

    current_seq += 1
    await conv_repo.add_message(
        conversation_id=conv_id,
        role="assistant",
        content=f"Sent via {request.tool_name}.",
        sequence_number=current_seq,
        metadata={"type": "hitl_send"},
    )

    await db.commit()

    return {"status": "sent", "result": result}
