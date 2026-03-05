"""
Tests for Phase 10.1 Graph Checkpointing.

Validates that AgentState serializes correctly to a checkpoint dictionary,
and that the Orchestrator can save, load, and clear checkpoints from Redis.
"""

import uuid
import pytest
import json
from unittest.mock import AsyncMock, patch
from chatbot_ai_system.orchestrator import AgentState, ChatOrchestrator, NODE_TOOL_EXECUTOR


@pytest.fixture
def sample_state():
    return AgentState(
        messages=[],
        user_input="Test",
        model="test-model",
        temperature=0.7,
        max_tokens=1000,
        conv_uuid=uuid.uuid4(),
        current_seq=10,
        start_time=0.0,
        phase="MEDIUM",
        intent="FILESYSTEM",
    )


def test_agent_state_to_checkpoint(sample_state):
    """Test that AgentState serializes the correct resumable subset."""
    sample_state.checkpoint_id = "test-checkpoint-123"
    sample_state.active_agent = "tool_executor_medium"
    sample_state.handoff_history = ["router", "tool_executor_medium"]
    
    ckpt = sample_state.to_checkpoint(current_node=NODE_TOOL_EXECUTOR, step=3)
    
    assert ckpt["checkpoint_id"] == "test-checkpoint-123"
    assert ckpt["conv_uuid"] == str(sample_state.conv_uuid)
    assert ckpt["current_node"] == NODE_TOOL_EXECUTOR
    assert ckpt["step"] == 3
    assert ckpt["current_seq"] == 10
    assert ckpt["phase"] == "MEDIUM"
    assert ckpt["active_agent"] == "tool_executor_medium"
    assert ckpt["handoff_history"] == ["router", "tool_executor_medium"]
    assert ckpt["status"] == "running"
    
    # Verify non-serializable fields are excluded
    assert "messages" not in ckpt
    assert "tools" not in ckpt
    
    # Ensure it's JSON serializable
    assert json.dumps(ckpt)


@pytest.mark.asyncio
async def test_checkpoint_save_load_clear(sample_state):
    """Test the orchestration checkpoint methods flow."""
    orch = ChatOrchestrator(
        provider=AsyncMock(),
        registry=AsyncMock(),
        conversation_repo=AsyncMock(),
        memory_repo=AsyncMock()
    )
    
    sample_state.checkpoint_id = "test-123"
    conv_id = sample_state.conv_uuid

    # Mock RedisClient
    with patch("chatbot_ai_system.database.redis.redis_client") as mock_redis:
        mock_redis.set = AsyncMock()
        mock_redis.get = AsyncMock(return_value={"status": "running", "current_node": NODE_TOOL_EXECUTOR})
        mock_redis.delete = AsyncMock()

        # 1. Save
        await orch._save_checkpoint(sample_state, NODE_TOOL_EXECUTOR, 1)
        mock_redis.set.assert_called_once()
        
        # 2. Load
        # Mock scan_iter to return our key
        async def async_gen():
            yield f"checkpoint:{conv_id}:test-123"
            
        mock_redis._redis.scan_iter.return_value = async_gen()
        
        loaded = await orch._load_checkpoint(conv_id)
        assert loaded is not None
        assert loaded["current_node"] == NODE_TOOL_EXECUTOR
        
        # 3. Clear
        await orch._clear_checkpoint(conv_id, "test-123")
        mock_redis.delete.assert_called_once_with(f"checkpoint:{conv_id}:test-123")
