import asyncio
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock

from chatbot_ai_system.models.schemas import ChatMessage, MessageRole


@pytest.mark.asyncio
async def test_thread_isolation_canary(test_client, mock_provider, mock_db_session, mock_conversation_repo):
    """
    Test Thread Isolation:
    Inject a canary secret into User A's thread.
    Verify User B cannot access or receive the canary in their response.
    """
    from chatbot_ai_system.server.routes import router
    from chatbot_ai_system.database.session import get_db

    # Canary setup
    secret_canary = "SECRET_CANARY_ABC123"
    
    # User A context
    conv_a_id = uuid.uuid4()
    mock_msg_a = MagicMock()
    mock_msg_a.role = MessageRole.SYSTEM
    mock_msg_a.content = f"User A secret context: {secret_canary}"
    
    # We must patch the router dependencies to simulate different conversations
    async def get_recent_messages_mock(conv_id, **kwargs):
        if str(conv_id) == str(conv_a_id):
            return [mock_msg_a]
        return []

    mock_conversation_repo.get_recent_messages.side_effect = get_recent_messages_mock

    mock_conversation_repo.get_recent_messages.side_effect = get_recent_messages_mock

    # 1. User B makes a request asking for secrets
    conv_b_id = uuid.uuid4()
    
    # Mock LLM to echo back context it receives to detect leakage
    async def echo_context_stream(*args, **kwargs):
        messages = args[0] if args else kwargs.get('messages', [])
        context_str = " ".join([m.content for m in messages])
        from chatbot_ai_system.models.schemas import StreamChunk
        yield StreamChunk(content=f"I see: {context_str}", done=True)
        
    mock_provider.mock_stream_responses = [echo_context_stream]

    # User B request
    response_b = test_client.post(
        "/api/chat",
        json={
            "conversation_id": str(conv_b_id),
            "messages": [{"role": "user", "content": "What secrets do you know?"}],
            "provider": "mock_model"
        }
    )

    assert response_b.status_code == 200, response_b.text
    
    # If it's a streaming SSE response, we read the text
    response_text = response_b.text
    
    # Assert Absolute Failure to leak
    assert secret_canary not in response_text, "CRITICAL: Thread isolation failed! User B accessed User A's canary."


@pytest.mark.asyncio
async def test_concurrent_interleaving(test_client, mock_provider, mock_db_session, mock_conversation_repo):
    """
    Test Concurrent Request Interleaving:
    Fire 5 requests simultaneously for 5 different conversations.
    Ensure messages aren't crossed in the orchestrator.
    """
    
    # Make the Mock LLM return the conversation ID it was asked about
    async def echo_conv_id_stream(messages, *args, **kwargs):
        # We need to find the user message to echo it back
        user_msg = next((m.content for m in messages if "Token" in m.content), "Unknown")
        from chatbot_ai_system.models.schemas import StreamChunk
        # Simulate slight async delay
        await asyncio.sleep(0.1) 
        yield StreamChunk(content=f"Response for {user_msg}", done=True)
        
    mock_provider.mock_stream_responses = echo_conv_id_stream

    async def make_request(ac, token_id: int):
        res = await ac.post(
            "/api/chat",
            json={
                "conversation_id": str(uuid.uuid4()),
                "messages": [{"role": "user", "content": f"Token_{token_id}"}],
                "provider": "mock_model"
            }
        )
        return token_id, res

    from chatbot_ai_system.server.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        tasks = [make_request(ac, i) for i in range(5)]
        results = await asyncio.gather(*tasks)

    for token_id, response in results:
        assert response.status_code == 200
        assert f"Token_{token_id}" in response.text
        # Ensure no other tokens leaked into this response
        for other_id in range(5):
            if other_id != token_id:
                assert f"Token_{other_id}" not in response.text


def test_context_overflow_2mb_payload(test_client, mock_provider, mock_db_session):
    """
    Test Context Overflow:
    Send a massive single message (2MB).
    FastAPI should reject it (413) or orchestrator trims it gracefully.
    We don't want a 500 error from token calculation crashing.
    """
    massive_payload = "A" * (2 * 1024 * 1024)  # 2MB of 'A'
    
    async def stream_ok(*args, **kwargs):
        from chatbot_ai_system.models.schemas import StreamChunk
        yield StreamChunk(content="Handled", done=True)
        
    mock_provider.mock_stream_responses = [stream_ok]

    response = test_client.post(
        "/api/chat",
        json={
            "conversation_id": str(uuid.uuid4()),
            "messages": [{"role": "user", "content": massive_payload}],
            "provider": "mock_model"
        }
    )

    # Either it gets rejected safely (413 Payload Too Large / 400 Bad Request)
    # OR it processes it safely (200 OK)
    # It absolutely MUST NOT be a 500 Internal Server Error
    assert response.status_code in [200, 400, 413, 422], response.text
    assert response.status_code != 500
