import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text as sa_text
from sqlalchemy.orm import selectinload

from chatbot_ai_system.database.models import Conversation, Message
from chatbot_ai_system.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class ConversationRepository(BaseRepository[Conversation]):
    """Repository for Conversation-related operations."""

    def __init__(self, session):
        super().__init__(session, Conversation)

    async def get_conversation_with_messages(self, conversation_id: UUID) -> Optional[Conversation]:
        """Get conversation with all messages loaded."""
        statement = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages))
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_conversation(self, user_id: UUID, title: Optional[str] = None) -> Conversation:
        """Create a new conversation for a user."""
        return await self.create(user_id=user_id, title=title)

    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        sequence_number: int,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        # Observability
        token_count_prompt: Optional[int] = None,
        token_count_completion: Optional[int] = None,
        model: Optional[str] = None,
        latency_ms: Optional[int] = None,
        finish_reason: Optional[str] = None,
    ) -> Message:
        """Add a message to a conversation."""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence_number=sequence_number,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            metadata_=metadata,
            token_count_prompt=token_count_prompt,
            token_count_completion=token_count_completion,
            model=model,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )
        self.session.add(message)
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def get_user_conversations(self, user_id: UUID, limit: int = 50) -> List[Conversation]:
        """Get all conversations for a user, ordered by update time."""
        statement = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_recent_messages(self, conversation_id: UUID, limit: int = 50) -> List[Message]:
        """Get the most recent messages for a conversation (Sliding Window)."""
        # Fetch in descending order (newest first) to get the "tail", then reverse.
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence_number.desc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        messages = result.scalars().all()
        return list(reversed(messages))

    async def get_next_sequence_number(self, conversation_id: UUID) -> int:
        """Fix 1.3: Atomically get next sequence number for a conversation via DB MAX."""
        result = await self.session.execute(
            sa_text(
                "SELECT COALESCE(MAX(sequence_number), 0) + 1 "
                "FROM messages WHERE conversation_id = :cid"
            ),
            {"cid": str(conversation_id)},
        )
        return result.scalar()

    async def update_summary(self, conversation_id: UUID, summary: str, last_seq_id: int) -> None:
        """Update the conversation summary and the last summarized sequence ID."""
        conv = await self.get(conversation_id)
        if conv:
            conv.summary = summary
            conv.last_summarized_seq_id = last_seq_id
            await self.session.flush()

    async def get_conversation_summary(self, conversation_id: UUID) -> Optional[dict]:
        """Get the summary and last summarized sequence ID."""
        statement = select(Conversation).where(Conversation.id == conversation_id)
        result = await self.session.execute(statement)
        conv = result.scalar_one_or_none()
        if conv:
            return {"summary": conv.summary, "last_summarized_seq_id": conv.last_summarized_seq_id}
        return None

    async def update_message_embedding(self, message_id: UUID, embedding: List[float]) -> None:
        """Update a message with its vector embedding."""
        # Safety check: Avoid writing vector data to a potentially non-vector column
        if not await self._check_vector_ext():
            logger.info(f"Skipping embedding update for {message_id}: pgvector not ready.")
            return

        statement = select(Message).where(Message.id == message_id)
        result = await self.session.execute(statement)
        message = result.scalar_one_or_none()
        if message:
            message.embedding = embedding
            await self.session.flush()

    async def _check_vector_ext(self) -> bool:
        """Check if pgvector extension is available and embedding is a vector."""
        try:
            # Check if extension exists
            res = await self.session.execute(sa_text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'"))
            if res.scalar() == 0:
                return False
            # Check if column is actually a vector (not text)
            res = await self.session.execute(sa_text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'messages' AND column_name = 'embedding'"
            ))
            dtype = res.scalar()
            return dtype == 'USER-DEFINED' # 'vector' shows up as USER-DEFINED in info schema usually
        except Exception:
            return False

    async def search_similar_messages(
        self, user_id: UUID, query_embedding: List[float], limit: int = 5, threshold: float = 0.7
    ) -> List[Message]:
        """Perform semantic search across all of a user's conversations."""
        # Feature detection to avoid Aborted Transaction on missing pgvector
        if not await self._check_vector_ext():
            logger.warning("pgvector not available or misconfigured. Skipping semantic search.")
            return []

        try:
            # Note: We join with Conversation to ensure we filter by the correct user_id
            statement = (
                select(Message)
                .join(Conversation)
                .where(Conversation.user_id == user_id)
                .where(Message.embedding.cosine_distance(query_embedding) < (1 - threshold))
                .order_by(Message.embedding.cosine_distance(query_embedding))
                .limit(limit)
            )
            result = await self.session.execute(statement)
            return list(result.scalars().all())
        except Exception as e:
            logger.error(f"Semantic search failed at runtime: {e}")
            await self.session.rollback()
            return []
