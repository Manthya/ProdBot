"""
Voice & Upload Routes — Phase 5.0

Provides:
- POST /api/upload — Upload media files (images, audio, video)
- POST /api/chat/multimodal — Chat with media attachments
- WebSocket /api/voice/stream — Real-time voice conversation
"""

import io
import logging
import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from chatbot_ai_system.config import get_settings
from chatbot_ai_system.models.schemas import (
    ChatMessage,
    MessageRole,
)
from chatbot_ai_system.services.media_pipeline import MediaPipeline
from chatbot_ai_system.services.stt_engine import STTEngine
from chatbot_ai_system.services.tts_engine import TTSEngine
from chatbot_ai_system.config.settings_manager import settings_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# Lazy singletons
_media_pipeline: Optional[MediaPipeline] = None
_stt_engine: Optional[STTEngine] = None
_tts_engine: Optional[TTSEngine] = None


def get_media_pipeline() -> MediaPipeline:
    global _media_pipeline
    if _media_pipeline is None:
        _media_pipeline = MediaPipeline()
    return _media_pipeline


def get_stt_engine() -> STTEngine:
    global _stt_engine
    if _stt_engine is None:
        _stt_engine = STTEngine()
    return _stt_engine


def get_tts_engine() -> TTSEngine:
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = TTSEngine()
    return _tts_engine


# ─── Response Models ────────────────────────────────────────────


class UploadResponse(BaseModel):
    """Response from media upload."""

    id: str
    type: str  # "image", "audio", "video"
    filename: str
    mime_type: str
    file_path: str
    file_size_bytes: int
    base64_data: Optional[str] = None  # For images
    transcription: Optional[str] = None  # For audio
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    keyframes: Optional[list] = None  # For video


class VoiceConfig(BaseModel):
    """Voice chat configuration."""

    stt_available: bool
    tts_available: bool
    tts_backend: str
    stt_model: str


# ─── Upload Endpoint ────────────────────────────────────────────


@router.post("/api/upload", response_model=UploadResponse)
async def upload_media(file: UploadFile = File(...)):
    """
    Upload a media file for processing.

    Supports images (png, jpg, gif, webp), audio (wav, mp3, ogg, m4a, webm),
    and video (mp4, webm, mov).

    For images: returns base64-encoded data ready for vision models.
    For audio: returns transcription from Whisper STT.
    For video: returns keyframes + audio transcription.
    """
    pipeline = get_media_pipeline()

    # Read file
    file_bytes = await file.read()
    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"

    # Validate
    try:
        file_type = pipeline.validate_upload(file_bytes, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Process based on type
    upload_id = str(uuid.uuid4())

    try:
        if file_type == "image":
            result = await pipeline.process_image(file_bytes, filename, content_type)
            return UploadResponse(
                id=upload_id,
                type="image",
                filename=filename,
                mime_type=content_type,
                file_path=result["file_path"],
                file_size_bytes=result["file_size_bytes"],
                base64_data=result["base64_data"],
                width=result["width"],
                height=result["height"],
            )

        elif file_type == "audio":
            result = await pipeline.process_audio(file_bytes, filename, content_type)
            return UploadResponse(
                id=upload_id,
                type="audio",
                filename=filename,
                mime_type=content_type,
                file_path=result["file_path"],
                file_size_bytes=result["file_size_bytes"],
                transcription=result["transcription"],
                duration_seconds=result["duration_seconds"],
            )

        elif file_type == "video":
            result = await pipeline.process_video(file_bytes, filename, content_type)
            return UploadResponse(
                id=upload_id,
                type="video",
                filename=filename,
                mime_type=content_type,
                file_path=result["file_path"],
                file_size_bytes=result["file_size_bytes"],
                transcription=result["transcription"],
                duration_seconds=result["duration_seconds"],
                width=result.get("width"),
                height=result.get("height"),
                keyframes=result.get("keyframes"),
            )

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Upload processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


# ─── Voice Config Endpoint ──────────────────────────────────────


@router.get("/api/voice/config", response_model=VoiceConfig)
async def voice_config():
    """Get voice chat configuration and availability."""
    settings = get_settings()
    tts = get_tts_engine()
    return VoiceConfig(
        stt_available=True,  # Whisper is always available
        tts_available=tts.is_available,
        tts_backend=tts._backend,
        stt_model=settings.stt_model,
    )


# ─── Voice WebSocket ────────────────────────────────────────────


@router.websocket("/api/voice/stream")
async def voice_stream(websocket: WebSocket):
    """
    Full-duplex voice conversation over WebSocket.

    Protocol:
    - Client sends: Binary audio frames (16kHz, 16-bit PCM, mono)
    - Client sends: JSON control messages {"type": "end_turn"}
    - Server sends: JSON {"type": "transcription", "text": "..."}
    - Server sends: JSON {"type": "response_start"}
    - Server sends: JSON {"type": "response_text", "text": "..."}
    - Server sends: Binary audio response chunks (WAV)
    - Server sends: JSON {"type": "response_end"}
    """
    await websocket.accept()
    logger.info("Voice WebSocket connected")

    stt = get_stt_engine()
    tts = get_tts_engine()
    audio_buffer = io.BytesIO()

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data:
                # Binary audio frame from client
                audio_buffer.write(data["bytes"])

            elif "text" in data:
                import json

                msg = json.loads(data["text"])

                if msg.get("type") == "end_turn":
                    # User finished speaking — process the audio
                    audio_buffer.seek(0)
                    audio_bytes = audio_buffer.read()
                    audio_buffer = io.BytesIO()  # Reset buffer

                    if len(audio_bytes) < 1600:  # Too short (~0.1s)
                        continue

                    # Step 1: STT
                    try:
                        stt_result = await stt.transcribe(audio_bytes)
                        transcription = stt_result["text"]

                        await websocket.send_json(
                            {
                                "type": "transcription",
                                "text": transcription,
                                "language": stt_result["language"],
                            }
                        )
                    except Exception as e:
                        logger.error(f"STT failed: {e}")
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": f"Speech recognition failed: {str(e)}",
                            }
                        )
                        continue

                    if not transcription.strip():
                        continue

                    # Step 2: Get LLM response
                    # Import here to avoid circular deps
                    from chatbot_ai_system.server.routes import get_provider

                    provider = get_provider("ollama")
                    settings = get_settings()

                    try:
                        await websocket.send_json({"type": "response_start"})

                        response = await provider.complete(
                            messages=[
                                ChatMessage(
                                    role=MessageRole.SYSTEM,
                                    content=(
                                        "You are a helpful voice assistant. "
                                        "Keep responses concise and conversational. "
                                        "Respond in 1-3 sentences."
                                    ),
                                ),
                                ChatMessage(
                                    role=MessageRole.USER,
                                    content=transcription,
                                ),
                            ],
                            model=await settings_manager.get_setting("ollama_model"),
                            temperature=0.7,
                            max_tokens=200,
                        )

                        response_text = response.message.content

                        # Send text
                        await websocket.send_json(
                            {
                                "type": "response_text",
                                "text": response_text,
                            }
                        )

                        # Step 3: TTS
                        if tts.is_available and response_text.strip():
                            try:
                                audio_response = await tts.synthesize(response_text)
                                if audio_response:
                                    # Send audio in chunks (64KB each)
                                    chunk_size = 65536
                                    for i in range(0, len(audio_response), chunk_size):
                                        await websocket.send_bytes(
                                            audio_response[i : i + chunk_size]
                                        )
                            except Exception as e:
                                logger.error(f"TTS failed: {e}")

                        await websocket.send_json({"type": "response_end"})

                    except Exception as e:
                        logger.error(f"LLM response failed: {e}")
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": f"Response generation failed: {str(e)}",
                            }
                        )

                elif msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected")
    except Exception as e:
        logger.error(f"Voice WebSocket error: {e}")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
