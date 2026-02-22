"""Plugin management routes for model and MCP server configuration."""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from chatbot_ai_system.config import get_settings
from chatbot_ai_system.config.mcp_server_config import MCPServerConfig, get_mcp_servers
from chatbot_ai_system.config.settings_manager import settings_manager
from chatbot_ai_system.models.schemas import ChatMessage, MessageRole, StreamChunk
from chatbot_ai_system.providers.factory import ProviderFactory
from chatbot_ai_system.tools import registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


# ────────────── Request / Response Schemas ──────────────


class AddModelRequest(BaseModel):
    """Request to add and activate a new model."""
    type: str = Field(..., description="'open_source' or 'paid'")
    provider: str = Field(..., description="e.g. 'ollama', 'openai', 'anthropic', 'gemini'")
    model: str = Field(..., description="Model name, e.g. 'llama3.2:latest'")
    api_key: Optional[str] = Field(None, description="API key for paid providers")
    base_url: Optional[str] = Field(None, description="Custom base URL (e.g. for Ollama)")


class AddModelResponse(BaseModel):
    success: bool
    message: str
    connectivity_ok: bool = False
    tools_ok: bool = False
    streaming_ok: bool = False
    details: List[str] = Field(default_factory=list)


class MCPServerRequest(BaseModel):
    """Request to add a new MCP server."""
    name: str
    command: str
    args: List[str]
    env_vars: Optional[Dict[str, str]] = None
    required_env_vars: Optional[List[str]] = None


class MCPServerInfo(BaseModel):
    name: str
    command: str
    args: List[str]
    status: str = "configured"


class PluginStatusResponse(BaseModel):
    active_model: str
    active_provider: str
    mcp_servers: List[MCPServerInfo]
    tool_count: int
    tools: List[str]


# ────────────── Endpoints ──────────────


@router.get("/status", response_model=PluginStatusResponse)
async def get_plugin_status():
    """Get current plugin status: active model, MCP servers, tools."""
    settings = get_settings()

    # Active model (check dynamic setting first)
    active_model = await settings_manager.get_setting("ollama_model") or settings.ollama_model
    active_provider = await settings_manager.get_setting("default_llm_provider") or settings.default_llm_provider

    # MCP servers
    servers = await get_mcp_servers()
    server_infos = []
    for s in servers:
        server_infos.append(MCPServerInfo(
            name=s.name,
            command=s.command,
            args=s.args,
            status="active",
        ))

    # Tools
    all_tools = registry.get_all_tools()
    tool_names = [t.name for t in all_tools]

    return PluginStatusResponse(
        active_model=active_model,
        active_provider=active_provider,
        mcp_servers=server_infos,
        tool_count=len(tool_names),
        tools=tool_names,
    )


@router.post("/add-model", response_model=AddModelResponse)
async def add_and_activate_model(request: AddModelRequest):
    """
    Add a new model and automatically set it as the active model.
    Runs connectivity, tool-calling, and streaming tests before activation.
    """
    details: List[str] = []
    connectivity_ok = False
    tools_ok = False
    streaming_ok = False

    # Save original env for cleanup
    original_env = {}

    # ── Capture previous model/provider for rollback ──
    previous_model = None
    previous_provider = None
    try:
        previous_model = await settings_manager.get_setting("ollama_model")
        previous_provider = await settings_manager.get_setting("default_llm_provider")
    except Exception:
        pass  # If we can't read previous settings, we still proceed

    try:
        # ── Step 1: Set up environment for the provider ──
        if request.api_key:
            key_map = {
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "gemini": "GOOGLE_API_KEY",
            }
            env_key = key_map.get(request.provider)
            if env_key:
                original_env[env_key] = os.environ.get(env_key)
                os.environ[env_key] = request.api_key

        if request.base_url and request.provider == "ollama":
            original_env["OLLAMA_BASE_URL"] = os.environ.get("OLLAMA_BASE_URL")
            os.environ["OLLAMA_BASE_URL"] = request.base_url

        # ── Step 2: Get provider ──
        # Clear cached instance so factory picks up new env
        if request.provider in ProviderFactory._instances:
            del ProviderFactory._instances[request.provider]

        provider = ProviderFactory.get_provider(request.provider)
        details.append(f"Provider '{request.provider}' initialized successfully.")

        # ── Step 3: Connectivity Test ──
        details.append("Running connectivity test...")
        test_message = ChatMessage(
            role=MessageRole.USER,
            content="Reply with just the word 'hello' and nothing else."
        )
        response_text = ""
        try:
            async for chunk in provider.stream(
                messages=[test_message],
                model=request.model,
                max_tokens=30,
            ):
                response_text += chunk.content
        except Exception as e:
            details.append(f"Connectivity FAILED: {str(e)}")
            return AddModelResponse(
                success=False,
                message=f"Model connectivity test failed: {str(e)}",
                connectivity_ok=False,
                tools_ok=False,
                streaming_ok=False,
                details=details,
            )

        if not response_text.strip():
            details.append("Connectivity FAILED: Empty response from model.")
            return AddModelResponse(
                success=False,
                message="Model returned an empty response.",
                connectivity_ok=False,
                details=details,
            )

        connectivity_ok = True
        streaming_ok = True  # If streaming worked, this passes too
        details.append(f"Connectivity PASSED. Response: '{response_text.strip()[:80]}'")
        details.append("Streaming PASSED.")

        # ── Step 4: Tool Calling Test ──
        details.append("Running tool calling test...")
        all_tools = registry.get_all_tools()
        if not all_tools:
            details.append("Tool test SKIPPED: No tools registered in registry.")
            tools_ok = True  # No tools = no tool test needed
        else:
            tool_defs = [t.to_ollama_format() for t in all_tools[:4]]  # Limit to first 4
            tool_message = ChatMessage(
                role=MessageRole.USER,
                content="What is the current time? Use a tool to find out."
            )
            found_tool_call = False
            try:
                async for chunk in provider.stream(
                    messages=[tool_message],
                    model=request.model,
                    tools=tool_defs,
                    max_tokens=150,
                ):
                    if chunk.tool_calls:
                        found_tool_call = True
                        details.append(f"Tool call detected: {chunk.tool_calls[0].function.name}")
                        break
            except Exception as e:
                details.append(f"Tool test produced an error (non-fatal): {str(e)}")

            if found_tool_call:
                tools_ok = True
                details.append("Tool calling PASSED.")
            else:
                details.append("Tool calling: No tool call detected. Model may not support tool calling natively.")
                tools_ok = False  # Warning but don't block

        # ── Step 5: Activate Model ──
        details.append("Activating model as current...")
        await settings_manager.set_setting("ollama_model", request.model, f"Set via plugin dashboard")
        await settings_manager.set_setting("default_llm_provider", request.provider, f"Set via plugin dashboard")

        # Store API key if provided
        if request.api_key:
            key_setting_map = {
                "openai": "openai_api_key",
                "anthropic": "anthropic_api_key",
                "gemini": "gemini_api_key",
            }
            setting_key = key_setting_map.get(request.provider)
            if setting_key:
                await settings_manager.set_setting(setting_key, request.api_key)

        details.append(f"Model '{request.model}' is now the active model on provider '{request.provider}'.")

        return AddModelResponse(
            success=True,
            message=f"Model '{request.model}' added and activated successfully.",
            connectivity_ok=connectivity_ok,
            tools_ok=tools_ok,
            streaming_ok=streaming_ok,
            details=details,
        )

    except Exception as e:
        logger.error(f"Add model failed: {e}")
        import traceback
        traceback.print_exc()
        details.append(f"Error: {str(e)}")

        # ── Rollback to previous model/provider if we had one ──
        if previous_model:
            try:
                await settings_manager.set_setting("ollama_model", previous_model, "Rollback after failed model switch")
                if previous_provider:
                    await settings_manager.set_setting("default_llm_provider", previous_provider, "Rollback after failed model switch")
                details.append(f"Rolled back to previous model: {previous_model} ({previous_provider})")
            except Exception as rb_err:
                logger.error(f"Rollback also failed: {rb_err}")
                details.append(f"WARNING: Rollback failed: {str(rb_err)}")

        return AddModelResponse(
            success=False,
            message=f"Failed to add model: {str(e)}",
            connectivity_ok=connectivity_ok,
            tools_ok=tools_ok,
            streaming_ok=streaming_ok,
            details=details,
        )

    finally:
        # ── Cleanup env ──
        for key, original_val in original_env.items():
            if original_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_val


@router.get("/mcp-servers", response_model=List[MCPServerInfo])
async def list_mcp_servers():
    """List all configured MCP servers."""
    servers = await get_mcp_servers()
    return [
        MCPServerInfo(
            name=s.name,
            command=s.command,
            args=s.args,
            status="active",
        )
        for s in servers
    ]


@router.post("/mcp-servers", response_model=MCPServerInfo)
async def add_mcp_server(request: MCPServerRequest):
    """Add a new MCP server configuration."""
    # Check for duplicate
    existing = await get_mcp_servers()
    if any(s.name == request.name for s in existing):
        raise HTTPException(status_code=409, detail=f"MCP server '{request.name}' already exists.")

    # Get current dynamic configs
    current_dynamic = await settings_manager.get_setting("mcp_servers") or []

    new_config = {
        "name": request.name,
        "command": request.command,
        "args": request.args,
        "env_vars": request.env_vars or {},
        "required_env_vars": request.required_env_vars or [],
    }
    current_dynamic.append(new_config)

    await settings_manager.set_setting("mcp_servers", current_dynamic, "Dynamic MCP server configs")

    # Try to register the new client and refresh tools
    try:
        from chatbot_ai_system.tools.mcp_client import MCPClient

        client = MCPClient(
            name=request.name,
            command=request.command,
            args=request.args,
            env=request.env_vars or os.environ.copy(),
        )
        registry.register_mcp_client(client)
        await registry.refresh_remote_tools()
    except Exception as e:
        logger.warning(f"MCP client registration warning (server added to config): {e}")

    return MCPServerInfo(
        name=request.name,
        command=request.command,
        args=request.args,
        status="active",
    )


@router.delete("/mcp-servers/{server_name}")
async def remove_mcp_server(server_name: str):
    """Remove an MCP server configuration."""
    current_dynamic = await settings_manager.get_setting("mcp_servers") or []

    updated = [c for c in current_dynamic if c.get("name") != server_name]

    if len(updated) == len(current_dynamic):
        raise HTTPException(status_code=404, detail=f"MCP server '{server_name}' not found in dynamic configs.")

    await settings_manager.set_setting("mcp_servers", updated, "Dynamic MCP server configs")

    return {"status": "removed", "server_name": server_name}
