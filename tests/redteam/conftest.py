import asyncio
import os
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from chatbot_ai_system.models.schemas import ChatMessage, MessageRole, StreamChunk, ToolCall
from chatbot_ai_system.providers.base import BaseLLMProvider
from chatbot_ai_system.tools.registry import ToolRegistry


# --- 0. Global Patches ---

@pytest.fixture(autouse=True)
def patch_tool_registry():
    """
    Prevents ToolRegistry from throwing an error if a tool is instantiated multiple times 
    during test collection/execution.
    """
    original_register = ToolRegistry.register
    
    def safe_register(self, tool):
        if tool.name not in self._tools:
            original_register(self, tool)
            
    with patch.object(ToolRegistry, 'register', new=safe_register):
        yield

# --- 1. Mock LLM Provider ---

class MockLLMProvider(BaseLLMProvider):
    """
    Mock LLM Provider that allows tests to inject specific responses
    for completion and streaming calls.
    """

    def __init__(self):
        super().__init__()
        self.provider_name = "mock_model"
        self.mock_complete_response = None
        self.mock_stream_responses = []
        self.stream_call_count = 0
        self.complete_call_count = 0
        self.last_stream_messages = []
        self.last_complete_messages = []

    def get_available_models(self) -> List[str]:
        return ["mock_model"]

    async def get_models(self) -> List[Dict[str, Any]]:
        return [{"name": "mock_model"}]

    def _try_parse_tool_calls(self, *args, **kwargs) -> List[Any]:
        return []

    async def health_check(self) -> bool:
        return True

    async def complete(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        tools: Optional[List[Dict]] = None,
    ) -> Any:
        self.complete_call_count += 1
        self.last_complete_messages = messages
        
        # Determine intent/complexity if it looks like a classifier prompt
        if messages and "You are a query analyzer" in messages[0].content:
            if callable(self.mock_complete_response):
                return await self.mock_complete_response(messages)
            
            # Default fallback for classifier
            resp = MagicMock()
            resp.message.content = "INTENT: GENERAL\nCOMPLEXITY: SIMPLE"
            return resp

        if callable(self.mock_complete_response):
            return await self.mock_complete_response(messages)
            
        return self.mock_complete_response

    async def stream(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        tools: Optional[List[Dict]] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        self.stream_call_count += 1
        self.last_stream_messages = messages

        # Allow tests to provide a list of async generators
        if isinstance(self.mock_stream_responses, list) and self.stream_call_count <= len(self.mock_stream_responses):
            generator = self.mock_stream_responses[self.stream_call_count - 1]
            if callable(generator):
                # If it's a factory function, call it
                async for chunk in generator(messages, tools):
                    yield chunk
            else:
                # If it's directly an iterable/generator of chunks
                for chunk in generator:
                    yield chunk
        elif callable(self.mock_stream_responses):
            async for chunk in self.mock_stream_responses(messages, tools):
                yield chunk
        else:
            # Default empty text response
            yield StreamChunk(content="Mocked text response", done=True)


@pytest.fixture
def mock_provider():
    """Returns a fresh instance of the MockLLMProvider."""
    return MockLLMProvider()


# --- 2. Database & Repository Mocks ---

@pytest.fixture
def mock_db_session():
    """Mock SQLAlchemy AsyncSession."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest_asyncio.fixture
async def mock_conversation_repo():
    """Mock ConversationRepository."""
    mock_repo = AsyncMock()
    
    async def create_mock(*args, **kwargs):
        mock_conv = MagicMock()
        mock_conv.id = uuid.uuid4()
        return mock_conv
        
    async def get_mock(conv_id):
        mock_conv = MagicMock()
        mock_conv.id = conv_id
        mock_conv.messages = []
        return mock_conv

    mock_repo.create_conversation.side_effect = create_mock
    mock_repo.get.side_effect = get_mock
    mock_repo.get_conversation_summary.return_value = None
    mock_repo.get_recent_messages.return_value = []
    
    # Store messages locally in the mock to simulate state
    messages_db = []
    
    async def add_message_mock(*args, **kwargs):
        msg = MagicMock()
        msg.id = uuid.uuid4()
        msg.role = kwargs.get('role', MessageRole.SYSTEM)
        msg.content = kwargs.get('content', '')
        messages_db.append(msg)
        return msg
        
    mock_repo.add_message.side_effect = add_message_mock
    mock_repo.get_next_sequence_number.return_value = len(messages_db) + 1
    
    return mock_repo


@pytest.fixture
def mock_memory_repo():
    """Mock MemoryRepository."""
    mock_repo = AsyncMock()
    mock_repo.get_user_memories.return_value = []
    return mock_repo


# --- 3. App & Test Client Integration ---

@pytest.fixture
def patched_app(mock_provider, mock_db_session, mock_conversation_repo, mock_memory_repo, monkeypatch):
    """
    Returns the FastAPI app with the LLM provider and DB dependencies mocked.
    """
    from chatbot_ai_system.server.main import app
    from chatbot_ai_system.server.routes import get_db, get_provider
    from chatbot_ai_system.database.redis import redis_client

    # Disable Redis calls
    monkeypatch.setattr(redis_client, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(redis_client, "set", AsyncMock(return_value=True))

    # Override get_provider dependency (it's a function, not a Depends injected dependency for the chat route internal logic)
    monkeypatch.setattr("chatbot_ai_system.server.routes.get_provider", lambda name: mock_provider)
    
    # Override repository instantiations and local auth wrappers in routes.py
    monkeypatch.setattr("chatbot_ai_system.server.routes.ConversationRepository", lambda db: mock_conversation_repo)
    monkeypatch.setattr("chatbot_ai_system.server.routes.MemoryRepository", lambda db: mock_memory_repo)
    monkeypatch.setattr("chatbot_ai_system.server.routes.ensure_user_exists", AsyncMock())
    
    async def mock_get_active_model_and_provider(*args, **kwargs):
        return "mock_model", "mock_model"
    monkeypatch.setattr("chatbot_ai_system.server.routes.get_active_model_and_provider", mock_get_active_model_and_provider)

    # Override get_db dependency
    app.dependency_overrides[get_db] = lambda: mock_db_session

    return app


@pytest.fixture
def test_client(patched_app):
    """Returns a TestClient wrapping the patched FastAPI app."""
    return TestClient(patched_app)

