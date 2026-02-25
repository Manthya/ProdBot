"""Pydantic schemas for the chatbot API."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """Role of the message sender."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCallFunction(BaseModel):
    name: str
    arguments: Dict[str, Any]


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    function: ToolCallFunction
    type: str = "function"


class MediaAttachment(BaseModel):
    """A media file attached to a message."""

    type: str  # "image", "audio", "video"
    url: Optional[str] = None  # stored file URL / path
    base64_data: Optional[str] = None  # inline base64 (for images sent to LLM)
    mime_type: str  # "image/png", "audio/wav", etc.
    filename: Optional[str] = None
    duration_seconds: Optional[float] = None  # for audio/video
    transcription: Optional[str] = None  # STT result for audio
    width: Optional[int] = None  # for images/video
    height: Optional[int] = None


class ChatMessage(BaseModel):
    """A single chat message."""

    role: MessageRole
    content: str
    attachments: Optional[List[MediaAttachment]] = None  # Phase 5.0: multimodal
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Optional[Dict[str, Any]] = None


class ChatRequest(BaseModel):
    """Request body for chat completions."""

    messages: List[ChatMessage]
    model: Optional[str] = None
    provider: Optional[str] = None
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    conversation_id: Optional[str] = None


class UsageInfo(BaseModel):
    """Token usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    """Response from chat completion."""

    message: ChatMessage
    usage: Optional[UsageInfo] = None
    model: str
    provider: str
    conversation_id: Optional[str] = None
    latency_ms: Optional[float] = None


class StreamChunk(BaseModel):
    """A single chunk in a streaming response."""

    content: str
    status: Optional[str] = None
    done: bool = False
    tool_calls: Optional[List[ToolCall]] = None
    conversation_id: Optional[str] = None
    usage: Optional[UsageInfo] = None
    audio_data: Optional[str] = None  # Phase 5.0: base64 TTS audio chunk


class ConversationInfo(BaseModel):
    """Information about a conversation."""

    id: str
    title: Optional[str] = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"
    version: str
    providers: Dict[str, bool] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorResponse(BaseModel):
    """Error response."""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
