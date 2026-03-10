import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from chatbot_ai_system.database.session import get_db
from chatbot_ai_system.models.schemas import StreamChunk, ToolCall, ToolCallFunction
from chatbot_ai_system.server.main import app


class TestToolIntegration(unittest.TestCase):
    def setUp(self):
        self.mock_db = AsyncMock()
        async def _override_get_db():
            yield self.mock_db
        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()

    @patch("chatbot_ai_system.server.routes.ChatOrchestrator")
    @patch("chatbot_ai_system.server.routes.ConversationRepository")
    @patch("chatbot_ai_system.server.routes.ensure_user_exists", new_callable=AsyncMock)
    @patch("chatbot_ai_system.server.routes.get_active_model_and_provider", new_callable=AsyncMock)
    @patch("chatbot_ai_system.server.routes.get_provider")
    def test_tool_calling_flow(
        self,
        mock_get_provider,
        mock_get_active_model_and_provider,
        _mock_ensure_user_exists,
        mock_conversation_repo_cls,
        mock_orchestrator_cls,
    ):
        mock_get_active_model_and_provider.return_value = ("qwen2.5:14b-instruct", "ollama")
        mock_provider = AsyncMock()
        mock_get_provider.return_value = mock_provider

        mock_conversation = SimpleNamespace(id=uuid.uuid4())
        mock_repo = AsyncMock()
        mock_repo.create_conversation.return_value = mock_conversation
        mock_repo.get_recent_messages.return_value = []
        mock_repo.get_next_sequence_number.return_value = 1
        mock_repo.add_message.return_value = SimpleNamespace(id=uuid.uuid4())
        mock_conversation_repo_cls.return_value = mock_repo

        async def fake_run(*args, **kwargs):
            tool_call = ToolCall(
                id="call_123",
                type="function",
                function=ToolCallFunction(name="get_current_time", arguments={}),
            )
            yield StreamChunk(content="", tool_calls=[tool_call], done=False)
            yield StreamChunk(content="It is 2026-02-10T14:00:00", done=True)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run = fake_run
        mock_orchestrator_cls.return_value = mock_orchestrator

        response = self.client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "What time is it?"}],
                "provider": "ollama"
            }
        )

        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["message"]["content"], "It is 2026-02-10T14:00:00")
        self.assertEqual(body["message"]["tool_calls"][0]["function"]["name"], "get_current_time")
        self.mock_db.commit.assert_awaited()

if __name__ == "__main__":
    unittest.main()
