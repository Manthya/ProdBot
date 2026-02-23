"""FastAPI server routes for the chatbot API."""

import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot_ai_system.config import get_settings
from chatbot_ai_system.database.session import get_db
from chatbot_ai_system.models.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ConversationInfo,
    ErrorResponse,
    HealthResponse,
    MessageRole,
    StreamChunk,
    ToolCall,
)
from chatbot_ai_system.orchestrator import ChatOrchestrator
from chatbot_ai_system.providers.base import BaseLLMProvider
from chatbot_ai_system.providers.factory import ProviderFactory
from chatbot_ai_system.repositories.conversation import ConversationRepository
from chatbot_ai_system.repositories.memory import MemoryRepository
from chatbot_ai_system.tools import registry
from chatbot_ai_system.config.settings_manager import settings_manager

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()


def get_provider(name: str = "ollama") -> BaseLLMProvider:
    """Get or create a provider instance."""
    return ProviderFactory.get_provider(name)


async def get_active_model_and_provider(settings) -> tuple:
    """Get the dynamically-set model and provider, falling back to static config."""
    active_model = await settings_manager.get_setting("ollama_model") or settings.ollama_model
    active_provider = await settings_manager.get_setting("default_llm_provider") or settings.default_llm_provider
    return active_model, active_provider


# Helper to simulate auth
def get_current_user_id() -> uuid.UUID:
    """Return a fixed user ID for single-tenant mode."""
    # Fixed UUID for 'default_user'
    return uuid.UUID("00000000-0000-0000-0000-000000000000")


async def ensure_user_exists(db: AsyncSession, user_id: uuid.UUID):
    """Ensure the default user exists in the database."""
    from sqlalchemy import select

    from chatbot_ai_system.database.models import User

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(id=user_id, email="guest@example.com", username="guest")
        db.add(user)
        await db.commit()
    return user


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    from chatbot_ai_system import __version__

    provider_status = {}

    # Check Ollama
    try:
        ollama = get_provider("ollama")
        provider_status["ollama"] = await ollama.health_check()
    except Exception:
        provider_status["ollama"] = False

    return HealthResponse(
        status="healthy" if any(provider_status.values()) else "degraded",
        version=__version__,
        providers=provider_status,
    )


@router.post("/api/chat", response_model=ChatResponse)
async def chat_completion(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Generate a chat completion."""
    settings = get_settings()
    active_model, active_provider = await get_active_model_and_provider(settings)
    provider_name = request.provider or active_provider
    user_id = get_current_user_id()

    # Ensure user exists (temporary hack until Auth phase)
    await ensure_user_exists(db, user_id)

    try:
        provider = get_provider(provider_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    conv_repo = ConversationRepository(db)
    mem_repo = MemoryRepository(db)

    # Get or create conversation
    if request.conversation_id:
        try:
            conversation_id = uuid.UUID(request.conversation_id)
            conversation = await conv_repo.get(conversation_id)
            if not conversation:
                conversation = await conv_repo.create_conversation(user_id=user_id)
        except ValueError:
            conversation = await conv_repo.create_conversation(user_id=user_id)
    else:
        conversation = await conv_repo.create_conversation(user_id=user_id)

    conversation_id = conversation.id

    # Load history (Sliding Window: Last 50 messages)
    # We need to render ChatMessage objects from DB Message objects
    # Phase 2.6: Use get_recent_messages
    db_messages = await conv_repo.get_recent_messages(conversation_id, limit=50)
    history = []
    if db_messages:
        for msg in db_messages:
            history.append(
                ChatMessage(
                    role=msg.role,
                    content=msg.content,
                    tool_calls=[ToolCall(**tc) for tc in msg.tool_calls]
                    if msg.tool_calls
                    else None,
                    tool_call_id=msg.tool_call_id,
                )
            )

    # Add user message to DB
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    last_msg = request.messages[-1]
    if last_msg.role != MessageRole.USER:
        raise HTTPException(status_code=400, detail="Last message must be from user")

    # Fix 1.3: Use atomic sequence numbers from DB
    current_seq = await conv_repo.get_next_sequence_number(conversation_id)

    # Check if duplicate (simple check)
    if not history or (
        history[-1].content != last_msg.content or history[-1].role != MessageRole.USER
    ):
        await conv_repo.add_message(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=last_msg.content,
            sequence_number=current_seq,
        )
        await db.commit()
        history.append(last_msg)  # Update local history for orchestrator

    try:
        # Initialize Orchestrator with repos
        orchestrator = ChatOrchestrator(
            provider=provider, registry=registry, conversation_repo=conv_repo, memory_repo=mem_repo
        )

        full_content = ""
        tool_calls = []

        # Run Orchestrator
        async for chunk in orchestrator.run(
            conversation_id=str(conversation_id),
            user_input=last_msg.content,
            conversation_history=history,
            model=request.model or active_model,
            temperature=request.temperature,
            max_tokens=request.max_tokens or 1000,
            user_id=str(user_id),
        ):
            full_content += chunk.content
            if chunk.tool_calls:
                tool_calls.extend(chunk.tool_calls)

        response_msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=full_content,
            tool_calls=tool_calls if tool_calls else None,
        )

        # Persist assistant message (Orchestrator saved it? NO. Orchestrator uses repo but does it commit? No.)
        # Orchestrator uses the SAME repo instance.
        # But wait, Orchestrator code:
        # await self.conversation_repo.add_message(...)
        # So Orchestrator adds it to session. We just need to commit ONE time at the end?
        # Yes, we should commit after orchestrator finishes.
        await db.commit()

        return ChatResponse(
            message=response_msg,
            usage=None,
            model=request.model or active_model,
            provider=provider_name,
            conversation_id=str(conversation_id),
        )

    except Exception as e:
        await db.rollback()
        logger.error(f"Chat completion error: {e}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to generate response: {e}")


@router.get("/api/conversations", response_model=List[ConversationInfo])
async def list_conversations(db: AsyncSession = Depends(get_db)):
    """List all conversations."""
    user_id = get_current_user_id()
    await ensure_user_exists(db, user_id)

    conv_repo = ConversationRepository(db)
    conversations = await conv_repo.get_user_conversations(user_id)

    result = []
    for conv in conversations:
        # We need to fetch the first message for title/preview?
        # The repo method `get_user_conversations` currently returns Conversation objects.
        # Ideally we join with messages or at least get the count/last update.
        # For efficiency, we might just return the title if set, else "Conversation <Date>"
        # DB schema has 'title'.

        # We need to load messages to get message_count efficiently or add a counter column.
        # For now, let's load them (lazy load might fail in async without eager load).
        # We should update repo to eager load or use separate query.
        # Let's rely on what we have.

        # A proper implementation would augment the query.
        # For now, let's just return basic info.
        result.append(
            ConversationInfo(
                id=str(conv.id),
                title=conv.title or f"Conversation {conv.created_at.strftime('%Y-%m-%d %H:%M')}",
                message_count=0,  # TODO: Optimize count query
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
        )
    return result


@router.get("/api/conversations/{conversation_id}", response_model=List[ChatMessage])
async def get_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    """Get messages for a specific conversation."""
    conv_id = uuid.UUID(conversation_id)
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_conversation_with_messages(conv_id)

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = []
    for msg in conversation.messages:
        messages.append(
            ChatMessage(
                role=msg.role,
                content=msg.content,
                tool_calls=[ToolCall(**tc) for tc in msg.tool_calls] if msg.tool_calls else None,
                tool_call_id=msg.tool_call_id,
                timestamp=msg.created_at,  # Add timestamp to schema if needed
            )
        )
    return messages


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a conversation."""
    conv_id = uuid.UUID(conversation_id)
    conv_repo = ConversationRepository(db)
    success = await conv_repo.delete(conv_id)

    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.commit()
    return {"status": "deleted", "conversation_id": conversation_id}


@router.websocket("/api/chat/stream")
async def websocket_chat_stream(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    """WebSocket endpoint for streaming chat responses."""
    await websocket.accept()
    settings = get_settings()
    is_connected = True

    user_id = get_current_user_id()
    await ensure_user_exists(db, user_id)

    conv_repo = ConversationRepository(db)
    mem_repo = MemoryRepository(db)
    orchestrator = None  # Fix 1.1: Track for cancellation

    try:
        while is_connected:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                is_connected = False
                break
            except Exception as e:
                logger.warning(f"Error receiving message: {e}")
                is_connected = False
                break

            try:
                request = ChatRequest(**data)
            except Exception as e:
                if is_connected:
                    await websocket.send_json(
                        ErrorResponse(error="Invalid request", detail=str(e)).model_dump()
                    )
                continue

            # Fix 3.1: Extract request_id from payload for correlation
            request_id = data.get("request_id")

            active_model, active_provider = await get_active_model_and_provider(settings)
            provider_name = request.provider or active_provider
            try:
                provider = get_provider(provider_name)
            except ValueError as e:
                if is_connected:
                    await websocket.send_json(
                        ErrorResponse(error="Invalid provider", detail=str(e)).model_dump()
                    )
                continue

            # Get or create conversation
            if request.conversation_id:
                try:
                    conv_uid = uuid.UUID(request.conversation_id)
                    conversation = await conv_repo.get(conv_uid)
                    if not conversation:
                        conversation = await conv_repo.create_conversation(user_id=user_id)
                except ValueError:
                    conversation = await conv_repo.create_conversation(user_id=user_id)
            else:
                conversation = await conv_repo.create_conversation(user_id=user_id)

            conversation_id = conversation.id

            # Load History (Sliding Window: Last 50 messages)
            db_messages = await conv_repo.get_recent_messages(conversation_id, limit=50)
            history = []
            if db_messages:
                for msg in db_messages:
                    history.append(
                        ChatMessage(
                            role=msg.role,
                            content=msg.content,
                            tool_calls=[ToolCall(**tc) for tc in msg.tool_calls]
                            if msg.tool_calls
                            else None,
                            tool_call_id=msg.tool_call_id,
                        )
                    )

            # Add User Message to DB
            user_msg = request.messages[-1]  # Assuming last message is new

            # Fix 1.3: Atomic sequence numbers from DB
            current_seq = await conv_repo.get_next_sequence_number(conversation_id)

            # Simple deduplication check in case client resends
            if not history or (
                history[-1].content != user_msg.content or history[-1].role != MessageRole.USER
            ):
                await conv_repo.add_message(
                    conversation_id=conversation_id,
                    role=MessageRole.USER,
                    content=user_msg.content,
                    sequence_number=current_seq,
                )
                await db.commit()
                history.append(user_msg)

            user_query = user_msg.content

            try:
                orchestrator = ChatOrchestrator(
                    provider=provider,
                    registry=registry,
                    conversation_repo=conv_repo,
                    memory_repo=mem_repo,
                )

                async for chunk in orchestrator.run(
                    conversation_id=str(conversation_id),
                    user_input=user_query,
                    conversation_history=history,
                    model=request.model or active_model,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens or 1000,
                    user_id=str(user_id),
                ):
                    if not is_connected:
                        # Fix 1.1: Cancel orchestrator on disconnect
                        if orchestrator:
                            orchestrator.cancel()
                        break

                    chunk.conversation_id = str(conversation_id)
                    # Fix 3.1: Echo request_id for client-side correlation
                    chunk_data = chunk.model_dump()
                    if request_id is not None:
                        chunk_data["request_id"] = request_id
                    try:
                        await websocket.send_json(chunk_data)
                    except Exception:
                        is_connected = False
                        break

                if is_connected:
                    await db.commit()
                    done_data = StreamChunk(
                        content="", done=True, conversation_id=str(conversation_id)
                    ).model_dump()
                    if request_id is not None:
                        done_data["request_id"] = request_id
                    await websocket.send_json(done_data)

            except Exception as e:
                await db.rollback()
                logger.error(f"Streaming error: {e}")
                import traceback

                traceback.print_exc()
                if is_connected:
                    await websocket.send_json(
                        ErrorResponse(error="Streaming failed", detail=str(e)).model_dump()
                    )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
        # Fix 1.1: Cancel any in-flight orchestrator
        if orchestrator:
            orchestrator.cancel()
    except Exception as e:
        await db.rollback()
        logger.error(f"WebSocket error: {e}")
    finally:
        logger.info("WebSocket connection closed")
