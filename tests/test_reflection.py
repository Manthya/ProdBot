"""
Test suite for the ReflectionHandler — Phase 10 Orchestrator Upgrade (Layer 5).

Tests:
1. Retry fires on tool execution failure, LLM returns corrected args
2. Retry fires up to MAX_RETRIES and then gives up gracefully
3. LLM returning SKIP stops retry
4. Malformed LLM responses are handled without crashing
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from chatbot_ai_system.services.reflection import ReflectionHandler, MAX_RETRIES
from chatbot_ai_system.models.schemas import ChatMessage, MessageRole


def _make_mock_provider(response_content: str) -> MagicMock:
    """Create a mock provider that returns a fixed LLM response."""
    provider = MagicMock()
    response = MagicMock()
    response.message.content = response_content
    provider.complete = AsyncMock(return_value=response)
    return provider


@pytest.mark.asyncio
async def test_reflection_corrects_tool_args():
    """Reflection should parse corrected tool call from LLM and return should_retry=True."""
    corrected = json.dumps({"name": "read_file", "arguments": {"path": "/correct/path.txt"}})
    provider = _make_mock_provider(corrected)
    handler = ReflectionHandler(provider=provider)

    should_retry, result = await handler.handle_error(
        tool_name="read_file",
        tool_args={"path": "/wrong/path.txt"},
        error="FileNotFoundError: /wrong/path.txt",
        model="test-model",
        attempt=0,
    )

    assert should_retry is True
    assert result is not None
    assert result["name"] == "read_file"
    assert result["arguments"]["path"] == "/correct/path.txt"


@pytest.mark.asyncio
async def test_reflection_max_retries_gives_up():
    """After MAX_RETRIES attempts, reflection should return should_retry=False."""
    provider = _make_mock_provider('{"name": "read_file", "arguments": {"path": "/x"}}')
    handler = ReflectionHandler(provider=provider)

    should_retry, result = await handler.handle_error(
        tool_name="read_file",
        tool_args={"path": "/bad"},
        error="Some error",
        model="test-model",
        attempt=MAX_RETRIES,  # Already at max
    )

    assert should_retry is False
    assert result is None
    # Provider should not have been called
    provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_reflection_skip_response():
    """When LLM says SKIP, reflection should not retry."""
    skip_response = json.dumps({"name": "SKIP", "reason": "Tool does not exist"})
    provider = _make_mock_provider(skip_response)
    handler = ReflectionHandler(provider=provider)

    should_retry, result = await handler.handle_error(
        tool_name="nonexistent_tool",
        tool_args={},
        error="ToolNotFoundError",
        model="test-model",
        attempt=0,
    )

    assert should_retry is False
    assert result is None


@pytest.mark.asyncio
async def test_reflection_malformed_response():
    """Malformed LLM response should return should_retry=False without crashing."""
    provider = _make_mock_provider("This is not JSON at all, just a rambling explanation.")
    handler = ReflectionHandler(provider=provider)

    should_retry, result = await handler.handle_error(
        tool_name="read_file",
        tool_args={"path": "/test"},
        error="Error",
        model="test-model",
        attempt=0,
    )

    assert should_retry is False
    assert result is None


@pytest.mark.asyncio
async def test_reflection_json_in_code_block():
    """Reflection should extract JSON from markdown code blocks."""
    response = '```json\n{"name": "search", "arguments": {"query": "fixed"}}\n```'
    provider = _make_mock_provider(response)
    handler = ReflectionHandler(provider=provider)

    should_retry, result = await handler.handle_error(
        tool_name="search",
        tool_args={"query": "broken"},
        error="ValueError",
        model="test-model",
        attempt=0,
    )

    assert should_retry is True
    assert result is not None
    assert result["name"] == "search"
    assert result["arguments"]["query"] == "fixed"


@pytest.mark.asyncio
async def test_reflection_provider_crash():
    """If the provider itself crashes during reflection, handle gracefully."""
    provider = MagicMock()
    provider.complete = AsyncMock(side_effect=RuntimeError("Connection reset"))
    handler = ReflectionHandler(provider=provider)

    should_retry, result = await handler.handle_error(
        tool_name="read_file",
        tool_args={"path": "/test"},
        error="Error",
        model="test-model",
        attempt=0,
    )

    assert should_retry is False
    assert result is None
