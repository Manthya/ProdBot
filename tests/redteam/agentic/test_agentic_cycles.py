import pytest
from unittest.mock import AsyncMock, patch

from chatbot_ai_system.models.schemas import ChatMessage, MessageRole, ToolCall, ToolCallFunction
from chatbot_ai_system.services.agentic_engine import AgenticEngine
from chatbot_ai_system.tools.registry import ToolRegistry


@pytest.fixture
def agentic_engine(mock_provider):
    registry = ToolRegistry()
    registry._tools.clear()  # Prevent double registration issues
    return AgenticEngine(mock_provider, registry)


@pytest.mark.asyncio
async def test_infinite_tool_loop_cycle_detection(agentic_engine, mock_provider):
    """
    Test Cyclic Deadlock:
    Mock the LLM to continuously call the exact SAME tool with the SAME arguments.
    Assert the Engine detects the cycle and forces an exit before MAX_AGENT_ROUNDS (8).
    """
    # Create an identical tool call that the mock LLM will yield infinitely
    identical_call = ToolCall(
        id="call_infinite",
        type="function",
        function=ToolCallFunction(name="get_current_time", arguments={})
    )

    async def infinite_tool_stream(*args, **kwargs):
        from chatbot_ai_system.models.schemas import StreamChunk
        yield StreamChunk(content="", tool_calls=[identical_call], done=True)
    
    # We set this as a factory returning generator
    mock_provider.mock_stream_responses = lambda m, t: infinite_tool_stream()

    messages = [ChatMessage(role=MessageRole.USER, content="What time is it in a loop?")]
    plan = ["Get the time"]
    
    tools = [{
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get time",
            "parameters": {}
        }
    }]

    # We mock the actual tool execution to just return a dummy value
    with patch.object(agentic_engine, "_execute_tool_with_retry", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = ("Time is now.", True)
        
        chunks = []
        async for chunk in agentic_engine.execute(
            messages=messages,
            model="mock",
            tools=tools,
            plan=plan
        ):
            chunks.append(chunk)

    # 1. Provide the plan
    # 2. Tool call -> detects cycle immediately on round 2
    # 3. Forces synthesis on round 3
    
    status_messages = [c.status for c in chunks if c.status]
    assert any("[CYCLE_DETECTED]" in m.content for m in messages if m.role == MessageRole.TOOL)
    assert len(status_messages) < 10, "Engine did not break the cycle quickly enough"


@pytest.mark.asyncio
async def test_circuit_breaker_consecutive_failures(agentic_engine, mock_provider):
    """
    Test Circuit Breaker:
    Mock every tool execution to FAIL.
    Assert the engine trips the breaker after MAX_CONSECUTIVE_FAILURES (2) and stops calling tools.
    """
    fail_call_1 = ToolCall(
        id="call_fail1",
        type="function",
        function=ToolCallFunction(name="get_current_time", arguments={"trigger": "fail1"})
    )
    fail_call_2 = ToolCall(
        id="call_fail2",
        type="function",
        function=ToolCallFunction(name="get_current_time", arguments={"trigger": "fail2"})
    )

    async def single_tool_stream1(*args, **kwargs):
        from chatbot_ai_system.models.schemas import StreamChunk
        yield StreamChunk(content="", tool_calls=[fail_call_1], done=True)
        
    async def single_tool_stream2(*args, **kwargs):
        from chatbot_ai_system.models.schemas import StreamChunk
        yield StreamChunk(content="", tool_calls=[fail_call_2], done=True)
        
    mock_provider.mock_stream_responses = [
        lambda m, t: single_tool_stream1(),
        lambda m, t: single_tool_stream2()
    ]

    messages = [ChatMessage(role=MessageRole.USER, content="Trigger failure")]
    plan = ["Fail step 1", "Fail step 2"]
    tools = [{"type": "function", "function": {"name": "get_current_time", "parameters": {}}}]

    with patch.object(agentic_engine, "_execute_tool_with_retry", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = ("Failed", False)  # return success=False
        
        chunks = []
        async for chunk in agentic_engine.execute(messages, "mock", tools, plan):
            chunks.append(chunk)

    status_messages = [c.status for c in chunks if c.status]
    assert any("⚠️ Multiple tool failures" in s for s in status_messages)
    # Ensure tool wasn't called more times than the circuit breaker threshold
    assert mock_exec.call_count == 2


@pytest.mark.asyncio
async def test_routing_bypass_tool_hallucination(agentic_engine, mock_provider):
    """
    Test Tool Hallucination:
    Mock LLM calling a tool that is NOT in the allowed list (or doesn't exist).
    Assert proper interception without engine crash.
    """
    hallucination_call = ToolCall(
        id="call_fake",
        type="function",
        function=ToolCallFunction(name="hack_pentagon", arguments={})
    )

    async def fake_tool_stream(*args, **kwargs):
        from chatbot_ai_system.models.schemas import StreamChunk
        # End loop by returning empty text on round 2
        if len(args[0]) > 2 and any(m.role == MessageRole.TOOL for m in args[0]):
             yield StreamChunk(content="I give up.", done=True)
        else:
             yield StreamChunk(content="", tool_calls=[hallucination_call], done=True)
        
    mock_provider.mock_stream_responses = fake_tool_stream

    messages = [ChatMessage(role=MessageRole.USER, content="Hack it")]
    plan = ["Hack"]
    tools = [{"type": "function", "function": {"name": "get_current_time", "parameters": {}}}]

    chunks = []
    async for chunk in agentic_engine.execute(messages, "mock", tools, plan):
        chunks.append(chunk)

    # Validate that [INVALID_TOOL] was injected into context
    assert any("[INVALID_TOOL]" in m.content for m in messages if m.role == MessageRole.TOOL)
