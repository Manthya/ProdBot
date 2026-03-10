#!/usr/bin/env python3
"""
Media Pipeline Test Script — Phase 5.0

Tests all multimodal capabilities step-by-step:
  M1: Image Upload (POST /api/upload)
  M2: Image + Vision Chat (POST /api/chat with llava:7b)
  M3: Audio Upload + STT (POST /api/upload with WAV)
  M4: Audio Chat (transcription injected into message)
  M5: Video Upload (keyframes extraction)
  M6: Voice Config (GET /api/voice/config)
  M7: Voice WebSocket (WS /api/voice/stream)
  M8: Upload Error Handling (bad file type, oversized)

Usage:
    PYTHONPATH=src .venv/bin/python scripts/test_media_pipeline.py
"""

import asyncio
import io
import json
import struct
import math
import time
import sys

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"

# ── Helpers ──────────────────────────────────────────────────────

results = []

def report(test_id: str, name: str, passed: bool, detail: str = "", duration: float = 0):
    status = "✅ PASS" if passed else "❌ FAIL"
    results.append((test_id, name, passed, detail))
    dur = f" ({duration:.1f}s)" if duration else ""
    print(f"  {status} {test_id}: {name}{dur}")
    if detail:
        # Truncate very long details
        if len(detail) > 300:
            detail = detail[:300] + "..."
        print(f"         → {detail}")
    print()


def create_test_png() -> bytes:
    """Create a simple 200x200 red-blue gradient PNG using PIL."""
    from PIL import Image
    img = Image.new("RGB", (200, 200))
    for x in range(200):
        for y in range(200):
            img.putpixel((x, y), (x % 256, 50, y % 256))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_test_wav(duration_sec: float = 2.0, freq_hz: int = 440) -> bytes:
    """Create a simple sine wave WAV file (16kHz, 16-bit, mono)."""
    sample_rate = 16000
    num_samples = int(sample_rate * duration_sec)
    amplitude = 16000  # ~50% volume

    # Generate sine wave samples
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        sample = int(amplitude * math.sin(2 * math.pi * freq_hz * t))
        samples.append(struct.pack("<h", max(-32768, min(32767, sample))))

    audio_data = b"".join(samples)

    # Build WAV header
    data_size = len(audio_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,       # file size - 8
        b"WAVE",
        b"fmt ",
        16,                   # chunk size
        1,                    # PCM format
        1,                    # mono
        sample_rate,
        sample_rate * 2,      # byte rate
        2,                    # block align
        16,                   # bits per sample
        b"data",
        data_size,
    )
    return header + audio_data


def create_test_mp4_stub() -> bytes:
    """Create a minimal MP4-like file (just enough to test upload validation)."""
    # This is NOT a valid MP4 — it's just enough bytes with .mp4 extension
    # to test the upload endpoint's file type detection
    # A real test would need a valid MP4 with video/audio tracks
    return b"\x00\x00\x00\x1c\x66\x74\x79\x70\x69\x73\x6f\x6d" + b"\x00" * 500


# ── Tests ────────────────────────────────────────────────────────

async def test_m1_image_upload():
    """M1: Upload a PNG image and verify processing."""
    import httpx
    start = time.time()
    try:
        png_bytes = create_test_png()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{BASE_URL}/api/upload",
                files={"file": ("test_image.png", png_bytes, "image/png")},
            )
        dur = time.time() - start
        
        if resp.status_code != 200:
            report("M1", "Image Upload", False, f"HTTP {resp.status_code}: {resp.text}", dur)
            return None
        
        data = resp.json()
        has_b64 = bool(data.get("base64_data"))
        has_path = bool(data.get("file_path"))
        w = data.get("width", 0)
        h = data.get("height", 0)
        
        passed = has_b64 and has_path and w > 0 and h > 0
        report(
            "M1", "Image Upload", passed,
            f"type={data.get('type')}, {w}x{h}, base64={len(data.get('base64_data',''))} chars, path={data.get('file_path')}",
            dur
        )
        return data
    except Exception as e:
        report("M1", "Image Upload", False, str(e), time.time() - start)
        return None


async def test_m2_vision_chat(upload_data: dict):
    """M2: Chat with an image attachment — vision model should describe it."""
    import httpx
    start = time.time()
    try:
        if not upload_data or not upload_data.get("base64_data"):
            report("M2", "Vision Chat", False, "Skipped — no image data from M1")
            return
        
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": "Describe this image in detail. What colors and patterns do you see?",
                    "attachments": [
                        {
                            "type": "image",
                            "base64_data": upload_data["base64_data"],
                            "mime_type": "image/png",
                            "filename": "test.png",
                            "width": upload_data.get("width"),
                            "height": upload_data.get("height"),
                        }
                    ],
                }
            ],
            "model": "llava:7b",
            "provider": "ollama",
        }
        
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{BASE_URL}/api/chat", json=payload)
        dur = time.time() - start
        
        if resp.status_code != 200:
            report("M2", "Vision Chat", False, f"HTTP {resp.status_code}: {resp.text[:200]}", dur)
            return
        
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        model_used = data.get("model", "")
        
        passed = len(content) > 20 and "llava" in model_used.lower()
        report(
            "M2", "Vision Chat", passed,
            f"model={model_used}, response={content[:150]}",
            dur
        )
    except Exception as e:
        report("M2", "Vision Chat", False, str(e), time.time() - start)


async def test_m3_audio_upload():
    """M3: Upload a WAV file and verify STT transcription."""
    import httpx
    start = time.time()
    try:
        wav_bytes = create_test_wav(duration_sec=2.0, freq_hz=440)
        
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{BASE_URL}/api/upload",
                files={"file": ("test_audio.wav", wav_bytes, "audio/wav")},
            )
        dur = time.time() - start
        
        if resp.status_code != 200:
            report("M3", "Audio Upload + STT", False, f"HTTP {resp.status_code}: {resp.text[:200]}", dur)
            return None
        
        data = resp.json()
        has_transcription = "transcription" in data
        has_duration = data.get("duration_seconds", 0) > 0
        has_path = bool(data.get("file_path"))
        
        report(
            "M3", "Audio Upload + STT", has_transcription and has_path,
            f"type={data.get('type')}, duration={data.get('duration_seconds', 0):.1f}s, "
            f"transcription='{data.get('transcription', '')[:100]}', path={data.get('file_path')}",
            dur
        )
        return data
    except Exception as e:
        report("M3", "Audio Upload + STT", False, str(e), time.time() - start)
        return None


async def test_m4_audio_chat(audio_data: dict):
    """M4: Chat with audio transcription injected into message."""
    import httpx
    start = time.time()
    try:
        # Use a known transcription string to test injection
        transcription = audio_data.get("transcription", "") if audio_data else ""
        if not transcription:
            transcription = "Hello, this is a test of the audio pipeline."
        
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": "I sent you an audio message. Please respond to it.",
                    "attachments": [
                        {
                            "type": "audio",
                            "mime_type": "audio/wav",
                            "filename": "test.wav",
                            "transcription": transcription,
                            "duration_seconds": 2.0,
                        }
                    ],
                }
            ],
            "model": "qwen2.5:14b-instruct",
            "provider": "ollama",
        }
        
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{BASE_URL}/api/chat", json=payload)
        dur = time.time() - start
        
        if resp.status_code != 200:
            report("M4", "Audio Chat", False, f"HTTP {resp.status_code}: {resp.text[:200]}", dur)
            return
        
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        
        passed = len(content) > 10
        report(
            "M4", "Audio Chat", passed,
            f"response={content[:150]}",
            dur
        )
    except Exception as e:
        report("M4", "Audio Chat", False, str(e), time.time() - start)


async def test_m5_video_upload():
    """M5: Upload a video file — test keyframe extraction (may fail without valid mp4)."""
    import httpx
    start = time.time()
    try:
        # Create a minimal test video using OpenCV
        video_bytes = _create_test_video()
        if not video_bytes:
            report("M5", "Video Upload", False, "Could not create test video", 0)
            return
        
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{BASE_URL}/api/upload",
                files={"file": ("test_video.mp4", video_bytes, "video/mp4")},
            )
        dur = time.time() - start
        
        if resp.status_code == 422:
            # Processing failed — expected if video is too minimal
            report("M5", "Video Upload", False, f"Processing failed (expected for synthetic video): {resp.text[:150]}", dur)
            return
        
        if resp.status_code != 200:
            report("M5", "Video Upload", False, f"HTTP {resp.status_code}: {resp.text[:200]}", dur)
            return
        
        data = resp.json()
        has_keyframes = len(data.get("keyframes", [])) > 0
        has_path = bool(data.get("file_path"))
        
        report(
            "M5", "Video Upload", has_path,
            f"type={data.get('type')}, keyframes={len(data.get('keyframes', []))}, "
            f"duration={data.get('duration_seconds', 0):.1f}s, "
            f"transcription='{(data.get('transcription') or '')[:80]}'",
            dur
        )
    except Exception as e:
        report("M5", "Video Upload", False, str(e), time.time() - start)


def _create_test_video() -> bytes:
    """Create a minimal valid MP4 video using OpenCV (3 seconds, colored frames)."""
    try:
        import cv2
        import numpy as np
        import tempfile
        import os
        
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = tmp.name
        tmp.close()
        
        # Create a 3-second video at 10fps (30 frames)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(tmp_path, fourcc, 10, (320, 240))
        
        for i in range(30):
            # Create frames with changing colors
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            frame[:, :, 0] = (i * 8) % 256     # Blue channel
            frame[:, :, 1] = 50                  # Green
            frame[:, :, 2] = (255 - i * 8) % 256 # Red
            # Add some text
            cv2.putText(frame, f"Frame {i}", (50, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            writer.write(frame)
        
        writer.release()
        
        with open(tmp_path, "rb") as f:
            data = f.read()
        os.unlink(tmp_path)
        return data
    except Exception as e:
        print(f"    ⚠️  Could not create test video: {e}")
        return None


async def test_m6_voice_config():
    """M6: Check voice config endpoint."""
    import httpx
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BASE_URL}/api/voice/config")
        dur = time.time() - start
        
        if resp.status_code != 200:
            report("M6", "Voice Config", False, f"HTTP {resp.status_code}: {resp.text}", dur)
            return
        
        data = resp.json()
        stt = data.get("stt_available", False)
        tts = data.get("tts_available", False)
        backend = data.get("tts_backend", "none")
        model = data.get("stt_model", "")
        
        report(
            "M6", "Voice Config", True,
            f"stt_available={stt}, tts_available={tts}, tts_backend={backend}, stt_model={model}",
            dur
        )
    except Exception as e:
        report("M6", "Voice Config", False, str(e), time.time() - start)


async def test_m7_voice_websocket():
    """M7: Test voice WebSocket — send audio, receive transcription + response."""
    start = time.time()
    try:
        import websockets
        
        wav_bytes = create_test_wav(duration_sec=1.5, freq_hz=440)
        
        async with websockets.connect(f"{WS_URL}/api/voice/stream", close_timeout=5) as ws:
            # Send audio data
            await ws.send(wav_bytes)
            
            # Signal end of turn
            await ws.send(json.dumps({"type": "end_turn"}))
            
            # Collect responses with timeout
            responses = []
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=60)
                    if isinstance(msg, str):
                        data = json.loads(msg)
                        responses.append(data)
                        if data.get("type") in ("response_end", "error"):
                            break
                    elif isinstance(msg, bytes):
                        responses.append({"type": "audio_chunk", "size": len(msg)})
            except asyncio.TimeoutError:
                pass
        
        dur = time.time() - start
        
        types = [r.get("type") for r in responses if isinstance(r, dict)]
        
        has_transcription = any(r.get("type") == "transcription" for r in responses)
        has_response = any(r.get("type") == "response_text" for r in responses)
        has_error = any(r.get("type") == "error" for r in responses)
        
        if has_error:
            error_msg = next(r.get("message", "") for r in responses if r.get("type") == "error")
            report("M7", "Voice WebSocket", False, f"Error: {error_msg}", dur)
        else:
            transcription = next((r.get("text", "") for r in responses if r.get("type") == "transcription"), "")
            response_text = next((r.get("text", "") for r in responses if r.get("type") == "response_text"), "")
            
            passed = has_transcription or has_response
            report(
                "M7", "Voice WebSocket", passed,
                f"events={types}, transcription='{transcription[:80]}', response='{response_text[:80]}'",
                dur
            )
    except Exception as e:
        report("M7", "Voice WebSocket", False, str(e), time.time() - start)


async def test_m8_error_handling():
    """M8: Test upload error handling — bad file type and missing file."""
    import httpx
    
    # M8a: Unsupported file type
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/api/upload",
                files={"file": ("evil.exe", b"MZ\x90\x00" * 100, "application/octet-stream")},
            )
        dur = time.time() - start
        passed = resp.status_code == 400
        report("M8a", "Reject Bad File Type", passed, f"HTTP {resp.status_code}: {resp.text[:100]}", dur)
    except Exception as e:
        report("M8a", "Reject Bad File Type", False, str(e), time.time() - start)
    
    # M8b: Unsupported extension
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/api/upload",
                files={"file": ("document.pdf", b"%PDF-1.4 test content", "application/pdf")},
            )
        dur = time.time() - start
        passed = resp.status_code == 400
        report("M8b", "Reject PDF Upload", passed, f"HTTP {resp.status_code}: {resp.text[:100]}", dur)
    except Exception as e:
        report("M8b", "Reject PDF Upload", False, str(e), time.time() - start)


# ── Main ─────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  📸 Media Pipeline Test Suite — Phase 5.0")
    print("=" * 60)
    print()
    
    # M1: Image Upload
    print("─── M1: Image Upload ───")
    upload_data = await test_m1_image_upload()
    
    # M2: Vision Chat
    print("─── M2: Image + Vision Chat (llava:7b) ───")
    await test_m2_vision_chat(upload_data)
    
    # M3: Audio Upload + STT
    print("─── M3: Audio Upload + STT ───")
    audio_data = await test_m3_audio_upload()
    
    # M4: Audio Chat
    print("─── M4: Audio Chat (transcription injection) ───")
    await test_m4_audio_chat(audio_data)
    
    # M5: Video Upload
    print("─── M5: Video Upload (keyframes) ───")
    await test_m5_video_upload()
    
    # M6: Voice Config
    print("─── M6: Voice Config ───")
    await test_m6_voice_config()
    
    # M7: Voice WebSocket
    print("─── M7: Voice WebSocket ───")
    await test_m7_voice_websocket()
    
    # M8: Error Handling
    print("─── M8: Upload Error Handling ───")
    await test_m8_error_handling()
    
    # Summary
    print()
    print("=" * 60)
    total = len(results)
    passed = sum(1 for _, _, p, _ in results if p)
    failed = total - passed
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
    print("=" * 60)
    
    if failed > 0:
        print("\n  Failed tests:")
        for tid, name, p, detail in results:
            if not p:
                print(f"    ❌ {tid}: {name}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
