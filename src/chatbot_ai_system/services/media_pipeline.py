"""
Media Processing Pipeline — Phase 5.0

Handles image, audio, and video processing for multimodal chat.
- Images: validate, resize, encode to base64
- Audio: convert to WAV, run STT (Whisper)
- Video: extract keyframes + audio track
- Storage: save files to local media/ directory
"""

import asyncio
import base64
import io
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from PIL import Image
from pydub import AudioSegment

from chatbot_ai_system.config import get_settings

logger = logging.getLogger(__name__)

# Maximum image dimension to send to vision model
MAX_IMAGE_DIM = 1024
# Maximum keyframes to extract from video
MAX_VIDEO_KEYFRAMES = 6
# Video keyframe interval in seconds
KEYFRAME_INTERVAL_SEC = 5


class MediaPipeline:
    """Central service for processing media attachments."""

    # Fix 3.4: Singleton Whisper model (class-level)
    _whisper_model = None
    _whisper_lock = asyncio.Lock()

    # Fix 3.3: Thread pool for CPU-bound work
    _executor = ThreadPoolExecutor(max_workers=2)

    def __init__(self):
        settings = get_settings()
        self.storage_path = Path(settings.media_storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = settings.max_upload_size_mb * 1024 * 1024
        self.supported_image_types = set(settings.supported_image_types.split(","))
        self.supported_audio_types = set(settings.supported_audio_types.split(","))
        self.supported_video_types = set(settings.supported_video_types.split(","))

    @classmethod
    async def _get_whisper_model(cls):
        """Fix 3.4: Lazy-load and cache the Whisper model as a singleton."""
        if cls._whisper_model is None:
            async with cls._whisper_lock:
                if cls._whisper_model is None:  # Double-check after acquiring lock
                    from faster_whisper import WhisperModel
                    settings = get_settings()
                    cls._whisper_model = WhisperModel(
                        settings.stt_model,
                        device=settings.stt_device,
                        compute_type="int8",
                    )
                    logger.info(f"Whisper model '{settings.stt_model}' loaded (singleton)")
        return cls._whisper_model

    # ─── Image Processing ───────────────────────────────────────────

    async def process_image(self, file_bytes: bytes, filename: str, mime_type: str) -> dict:
        """
        Process an image: validate, resize, encode to base64.
        Fix 3.3: Offloads CPU-bound work to thread pool.

        Returns:
            dict with keys: base64_data, width, height, file_path, file_size_bytes
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._process_image_sync, file_bytes, filename, mime_type
        )

    def _process_image_sync(self, file_bytes: bytes, filename: str, mime_type: str) -> dict:
        """Synchronous image processing (runs in thread pool)."""
        try:
            img = Image.open(io.BytesIO(file_bytes))
            original_width, original_height = img.size

            # Resize if too large (preserve aspect ratio)
            if max(original_width, original_height) > MAX_IMAGE_DIM:
                img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)
                logger.info(
                    f"Resized image from {original_width}x{original_height} "
                    f"to {img.size[0]}x{img.size[1]}"
                )

            # Convert to RGB if RGBA (strip alpha for LLM compatibility)
            if img.mode == "RGBA":
                img = img.convert("RGB")

            # Save to storage
            file_path = self._save_file(file_bytes, filename, "images")

            # Encode resized image to base64
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            base64_data = base64.b64encode(buffer.getvalue()).decode("utf-8")

            return {
                "base64_data": base64_data,
                "width": img.size[0],
                "height": img.size[1],
                "file_path": str(file_path),
                "file_size_bytes": len(file_bytes),
            }

        except Exception as e:
            logger.error(f"Image processing failed: {e}")
            raise ValueError(f"Failed to process image: {e}")

    # ─── Audio Processing ───────────────────────────────────────────

    async def process_audio(self, file_bytes: bytes, filename: str, mime_type: str) -> dict:
        """
        Process audio: convert to WAV, get duration, run STT.

        Returns:
            dict with keys: transcription, duration_seconds, file_path, file_size_bytes
        """
        try:
            # Save original file
            file_path = self._save_file(file_bytes, filename, "audio")

            # Convert to WAV for Whisper
            audio = AudioSegment.from_file(io.BytesIO(file_bytes))
            duration_seconds = len(audio) / 1000.0

            # Convert to 16kHz mono WAV for STT
            audio = audio.set_frame_rate(16000).set_channels(1)
            wav_buffer = io.BytesIO()
            audio.export(wav_buffer, format="wav")
            wav_bytes = wav_buffer.getvalue()

            # Run STT
            transcription = await self._transcribe_audio(wav_bytes)

            return {
                "transcription": transcription,
                "duration_seconds": duration_seconds,
                "file_path": str(file_path),
                "file_size_bytes": len(file_bytes),
            }

        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            raise ValueError(f"Failed to process audio: {e}")

    # ─── Video Processing ───────────────────────────────────────────

    async def process_video(self, file_bytes: bytes, filename: str, mime_type: str) -> dict:
        """
        Process video: extract keyframes + transcribe audio track.
        Fix 3.3: Offloads CPU-bound work to thread pool.

        Returns:
            dict with keys: keyframes (list of base64), transcription,
                           duration_seconds, file_path, file_size_bytes
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self._executor, self._process_video_sync, file_bytes, filename, mime_type
        )
        # Transcribe audio (uses singleton Whisper model)
        if result.get("_wav_bytes"):
            try:
                result["transcription"] = await self._transcribe_audio(result.pop("_wav_bytes"))
            except Exception as e:
                logger.warning(f"Could not transcribe video audio: {e}")
                result.pop("_wav_bytes", None)
        return result

    def _process_video_sync(self, file_bytes: bytes, filename: str, mime_type: str) -> dict:
        """Synchronous video processing (runs in thread pool)."""
        try:
            import tempfile

            import cv2

            # Save original file
            file_path = self._save_file(file_bytes, filename, "video")

            # Write to temp file for OpenCV
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name

            try:
                cap = cv2.VideoCapture(tmp_path)
                fps = cap.get(cv2.CAP_PROP_FPS) or 30
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                duration_seconds = total_frames / fps
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

                # Extract keyframes at intervals
                keyframes = []
                frame_interval = int(fps * KEYFRAME_INTERVAL_SEC)
                frame_idx = 0

                while cap.isOpened() and len(keyframes) < MAX_VIDEO_KEYFRAMES:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                    ret, frame = cap.read()
                    if not ret:
                        break

                    # Resize frame
                    h, w = frame.shape[:2]
                    if max(h, w) > MAX_IMAGE_DIM:
                        scale = MAX_IMAGE_DIM / max(h, w)
                        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))

                    # Encode to base64
                    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    b64 = base64.b64encode(buffer.tobytes()).decode("utf-8")
                    keyframes.append(b64)

                    frame_idx += frame_interval

                cap.release()
            finally:
                os.unlink(tmp_path)

            # Extract audio track as WAV for later async transcription
            wav_bytes = None
            try:
                audio = AudioSegment.from_file(io.BytesIO(file_bytes))
                audio = audio.set_frame_rate(16000).set_channels(1)
                wav_buffer = io.BytesIO()
                audio.export(wav_buffer, format="wav")
                wav_bytes = wav_buffer.getvalue()
            except Exception as e:
                logger.warning(f"Could not extract audio from video: {e}")

            return {
                "keyframes": keyframes,
                "transcription": None,
                "_wav_bytes": wav_bytes,  # Transient — consumed by async caller
                "duration_seconds": duration_seconds,
                "width": width,
                "height": height,
                "file_path": str(file_path),
                "file_size_bytes": len(file_bytes),
            }

        except Exception as e:
            logger.error(f"Video processing failed: {e}")
            raise ValueError(f"Failed to process video: {e}")

    # ─── STT (Whisper) ──────────────────────────────────────────────

    async def _transcribe_audio(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio using faster-whisper with singleton model."""
        import tempfile

        # Fix 3.4: Use singleton model instead of re-instantiating
        model = await self._get_whisper_model()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name

        try:
            # Fix 3.3: Run transcription in thread pool
            loop = asyncio.get_event_loop()
            segments, info = await loop.run_in_executor(
                self._executor,
                lambda: model.transcribe(tmp_path, beam_size=5)
            )
            # Consume segments iterator in thread pool too
            transcription = await loop.run_in_executor(
                self._executor,
                lambda: " ".join(segment.text.strip() for segment in segments)
            )
            logger.info(
                f"STT completed: {info.language} ({info.language_probability:.1%}), "
                f"{len(transcription)} chars"
            )
            return transcription
        finally:
            os.unlink(tmp_path)

    # ─── File Storage ───────────────────────────────────────────────

    def _save_file(self, file_bytes: bytes, filename: str, subdir: str) -> Path:
        """Save file to media storage directory."""
        ext = Path(filename).suffix or ".bin"
        unique_name = f"{uuid.uuid4().hex}{ext}"
        target_dir = self.storage_path / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / unique_name
        target_path.write_bytes(file_bytes)
        logger.info(f"Saved media file: {target_path} ({len(file_bytes)} bytes)")
        return target_path

    def get_file_type(self, filename: str) -> Optional[str]:
        """Determine file type from extension."""
        ext = Path(filename).suffix.lstrip(".").lower()
        if ext in self.supported_image_types:
            return "image"
        elif ext in self.supported_audio_types:
            return "audio"
        elif ext in self.supported_video_types:
            return "video"
        return None

    def validate_upload(self, file_bytes: bytes, filename: str) -> str:
        """Validate file size and type. Returns file type or raises ValueError."""
        if len(file_bytes) > self.max_upload_bytes:
            raise ValueError(
                f"File too large: {len(file_bytes)} bytes (max {self.max_upload_bytes} bytes)"
            )

        file_type = self.get_file_type(filename)
        if not file_type:
            ext = Path(filename).suffix
            raise ValueError(
                f"Unsupported file type: {ext}. "
                f"Supported: images ({', '.join(self.supported_image_types)}), "
                f"audio ({', '.join(self.supported_audio_types)}), "
                f"video ({', '.join(self.supported_video_types)})"
            )
        return file_type
