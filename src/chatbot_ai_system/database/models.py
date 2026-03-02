import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime (no tzinfo).
    
    This avoids the deprecated datetime.utcnow() while staying compatible
    with the existing DB schema which uses TIMESTAMP WITHOUT TIME ZONE.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _utcnow())
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    conversations: Mapped[List["Conversation"]] = relationship(back_populates="user")
    memories: Mapped[List["Memory"]] = relationship(back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _utcnow())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: _utcnow(), onupdate=lambda: _utcnow(), index=True
    )

    # Layer 2 Memory: Summarization (Phase 2.7)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_summarized_seq_id: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation", order_by="Message.sequence_number"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String)  # user, assistant, system, tool
    content: Mapped[str] = mapped_column(Text)

    # Store tool calls as JSONB list: [{"name": "...", "arguments": {...}}]
    tool_calls: Mapped[Optional[List[Any]]] = mapped_column(JSONB, nullable=True)

    # For tool results, link back to the call
    tool_call_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Metadata (tokens, latency, model_name)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    # Observability Metrics (Phase 2.5)
    token_count_prompt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_count_completion: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finish_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Phase 3: Vector Search (Cold Memory)
    # 768 dimensions for nomic-embed-text
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(768), nullable=True)

    sequence_number: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _utcnow())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    attachments: Mapped[List["MediaAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class MediaAttachment(Base):
    """Phase 5.0: Media files attached to messages."""

    __tablename__ = "media_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), index=True)
    type: Mapped[str] = mapped_column(String)  # "image", "audio", "video"
    mime_type: Mapped[str] = mapped_column(String)
    file_path: Mapped[str] = mapped_column(String)  # local or S3 path
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    transcription: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # STT for audio
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _utcnow())

    message: Mapped["Message"] = relationship(back_populates="attachments")


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    context: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    last_accessed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _utcnow())

    user: Mapped["User"] = relationship(back_populates="memories")
class SystemSetting(Base):
    """Stores dynamic system configuration."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: _utcnow(), onupdate=lambda: _utcnow()
    )
