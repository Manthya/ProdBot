"""
Dedicated Model Integration Test Script

Thoroughly validates a model's integration with the chatbot system.
Tests connectivity, tool calling, streaming, and system prompt compliance.

Usage:
    python3 scripts/test_model_integration.py <provider> <model> [api_key] [base_url]

Examples:
    python3 scripts/test_model_integration.py ollama qwen2.5:14b-instruct
    python3 scripts/test_model_integration.py ollama llama3.2:latest
    python3 scripts/test_model_integration.py openai gpt-4o-mini sk-abc123
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None  # API-based tests will be skipped

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from chatbot_ai_system.providers.factory import ProviderFactory
from chatbot_ai_system.models.schemas import ChatMessage, MessageRole
from chatbot_ai_system.tools import registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Test timeouts
CONNECTIVITY_TIMEOUT = 60  # seconds
TOOL_TIMEOUT = 90
STREAMING_TIMEOUT = 60
SYSTEM_PROMPT_TIMEOUT = 60


class TestResult:
    """Structured result for a single test phase."""

    def __init__(self, phase: str):
        self.phase = phase
        self.passed = False
        self.skipped = False
        self.details: List[str] = []
        self.error: Optional[str] = None
        self.duration_ms: float = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "passed": self.passed,
            "skipped": self.skipped,
            "details": self.details,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 1),
        }


async def phase_connectivity(
    provider, model: str, timeout: float = CONNECTIVITY_TIMEOUT
) -> TestResult:
    """Phase 1: Basic connectivity — send a simple prompt and check for non-empty response."""
    result = TestResult("connectivity")
    start = time.time()

    try:
        test_msg = ChatMessage(
            role=MessageRole.USER,
            content="Reply with just the word 'hello' and nothing else.",
        )
        response = ""

        async def _run():
            nonlocal response
            async for chunk in provider.stream(
                messages=[test_msg], model=model, max_tokens=30
            ):
                response += chunk.content

        await asyncio.wait_for(_run(), timeout=timeout)

        if not response.strip():
            result.error = "Model returned an empty response."
            result.details.append("Received empty response — model may be misconfigured.")
        else:
            result.passed = True
            result.details.append(f"Response: '{response.strip()[:100]}'")

    except asyncio.TimeoutError:
        result.error = f"Connectivity test timed out after {timeout}s."
        result.details.append("Model did not respond within timeout. Check if model is loaded.")
    except Exception as e:
        result.error = str(e)
        result.details.append(f"Exception: {e}")

    result.duration_ms = (time.time() - start) * 1000
    return result


async def phase_tool_calling(
    provider, model: str, timeout: float = TOOL_TIMEOUT
) -> TestResult:
    """Phase 2: Tool calling — verify model can generate tool call JSON."""
    result = TestResult("tool_calling")
    start = time.time()

    # Guard: check for available tools
    all_tools = registry.get_all_tools()
    if not all_tools:
        result.skipped = True
        result.passed = True
        result.details.append("No tools in registry — tool test skipped.")
        result.duration_ms = (time.time() - start) * 1000
        return result

    # Use first 4 tools to avoid overwhelming the model
    tool_defs = [t.to_ollama_format() for t in all_tools[:4]]
    tool_names = [t.name for t in all_tools[:4]]
    result.details.append(f"Testing with tools: {tool_names}")

    # Attempt 1: Clear tool-requiring prompt
    prompts = [
        "What is the current time right now? Use the appropriate tool to answer.",
        "Search the web for 'latest news today'. You MUST use a tool.",
    ]

    found_tool_call = False
    for i, prompt in enumerate(prompts):
        if found_tool_call:
            break
        try:
            msg = ChatMessage(role=MessageRole.USER, content=prompt)

            async def _run():
                nonlocal found_tool_call
                async for chunk in provider.stream(
                    messages=[msg], model=model, tools=tool_defs, max_tokens=200
                ):
                    if chunk.tool_calls:
                        found_tool_call = True
                        tc = chunk.tool_calls[0]
                        result.details.append(
                            f"Attempt {i + 1}: Tool call detected — {tc.function.name}({tc.function.arguments})"
                        )
                        break

            await asyncio.wait_for(_run(), timeout=timeout)

            if not found_tool_call:
                result.details.append(f"Attempt {i + 1}: No tool call from prompt '{prompt[:50]}...'")

        except asyncio.TimeoutError:
            result.details.append(f"Attempt {i + 1}: Timed out after {timeout}s.")
        except Exception as e:
            result.details.append(f"Attempt {i + 1}: Error — {str(e)}")

    if found_tool_call:
        result.passed = True
    else:
        result.error = "Model did not generate any tool calls across all attempts."
        result.details.append(
            "This model may not support native tool calling. "
            "It can still be used for plain chat."
        )

    result.duration_ms = (time.time() - start) * 1000
    return result


async def phase_streaming(
    provider, model: str, timeout: float = STREAMING_TIMEOUT
) -> TestResult:
    """Phase 3: Streaming — verify chunks arrive incrementally."""
    result = TestResult("streaming")
    start = time.time()

    try:
        msg = ChatMessage(
            role=MessageRole.USER,
            content="Count from 1 to 5, one number per line.",
        )
        chunk_count = 0
        total_content = ""

        async def _run():
            nonlocal chunk_count, total_content
            async for chunk in provider.stream(
                messages=[msg], model=model, max_tokens=100
            ):
                if chunk.content:
                    chunk_count += 1
                    total_content += chunk.content

        await asyncio.wait_for(_run(), timeout=timeout)

        result.details.append(f"Received {chunk_count} content chunks.")
        result.details.append(f"Total content length: {len(total_content)} chars.")

        if chunk_count >= 2:
            result.passed = True
            result.details.append("Streaming works — multiple chunks received.")
        elif chunk_count == 1:
            result.passed = True
            result.details.append(
                "Only 1 chunk received — model may buffer entire response. Still functional."
            )
        else:
            result.error = "No content chunks received."

    except asyncio.TimeoutError:
        result.error = f"Streaming test timed out after {timeout}s."
    except Exception as e:
        result.error = str(e)
        result.details.append(f"Exception: {e}")

    result.duration_ms = (time.time() - start) * 1000
    return result


async def phase_system_prompt(
    provider, model: str, timeout: float = SYSTEM_PROMPT_TIMEOUT
) -> TestResult:
    """Phase 4: System prompt compliance — verify model follows system instructions."""
    result = TestResult("system_prompt")
    start = time.time()

    try:
        system_msg = ChatMessage(
            role=MessageRole.SYSTEM,
            content="You are a helpful assistant. You must always end your responses with the phrase 'END_MARKER'.",
        )
        user_msg = ChatMessage(
            role=MessageRole.USER,
            content="What is 2 + 2?",
        )
        response = ""

        async def _run():
            nonlocal response
            async for chunk in provider.stream(
                messages=[system_msg, user_msg], model=model, max_tokens=100
            ):
                response += chunk.content

        await asyncio.wait_for(_run(), timeout=timeout)

        result.details.append(f"Response: '{response.strip()[:150]}'")

        if "END_MARKER" in response:
            result.passed = True
            result.details.append("System prompt compliance PASSED — marker found.")
        else:
            result.passed = False
            result.error = "Model did not follow system prompt instruction (marker not found)."
            result.details.append(
                "This is a soft failure — model may still work for general chat."
            )

    except asyncio.TimeoutError:
        result.error = f"System prompt test timed out after {timeout}s."
    except Exception as e:
        result.error = str(e)
        result.details.append(f"Exception: {e}")

    result.duration_ms = (time.time() - start) * 1000
    return result


# ────────────── API-Based Safety Tests (Phases 5–8) ──────────────


def phase_invalid_model_rejection(api_url: str) -> TestResult:
    """Phase 5: POST add-model with a non-existent model — expect graceful failure."""
    result = TestResult("invalid_model_rejection")
    start = time.time()

    if not requests:
        result.skipped = True
        result.passed = True
        result.details.append("Skipped: 'requests' package not installed.")
        result.duration_ms = (time.time() - start) * 1000
        return result

    try:
        resp = requests.post(
            f"{api_url}/api/plugins/add-model",
            json={
                "type": "open_source",
                "provider": "ollama",
                "model": "fake-model-999:nonexistent",
                "base_url": "http://localhost:11434",
            },
            timeout=30,
        )
        result.details.append(f"Status: {resp.status_code}")
        data = resp.json()
        result.details.append(f"success={data.get('success')}, message={data.get('message', '')[:100]}")

        if resp.status_code == 200 and data.get("success") is False:
            result.passed = True
            result.details.append("Invalid model correctly rejected with success=false.")
        elif resp.status_code >= 400:
            result.passed = True
            result.details.append(f"Invalid model rejected with HTTP {resp.status_code}.")
        else:
            result.error = "Invalid model was NOT rejected — this is a safety bug!"

    except requests.ConnectionError:
        result.error = f"Cannot connect to {api_url}. Is the server running?"
    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def phase_bad_provider_rejection(api_url: str) -> TestResult:
    """Phase 6: POST add-model with an invalid provider — expect rejection."""
    result = TestResult("bad_provider_rejection")
    start = time.time()

    if not requests:
        result.skipped = True
        result.passed = True
        result.details.append("Skipped: 'requests' package not installed.")
        result.duration_ms = (time.time() - start) * 1000
        return result

    try:
        resp = requests.post(
            f"{api_url}/api/plugins/add-model",
            json={
                "type": "paid",
                "provider": "totally_invalid_provider_xyz",
                "model": "some-model",
                "api_key": "fake-key-12345678",
            },
            timeout=15,
        )
        result.details.append(f"Status: {resp.status_code}")
        data = resp.json()
        result.details.append(f"success={data.get('success')}, message={data.get('message', '')[:100]}")

        if data.get("success") is False or resp.status_code >= 400:
            result.passed = True
            result.details.append("Bad provider correctly rejected.")
        else:
            result.error = "Bad provider was NOT rejected — this is a safety bug!"

    except requests.ConnectionError:
        result.error = f"Cannot connect to {api_url}."
    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def phase_chat_continuity(api_url: str) -> TestResult:
    """Phase 7: Send a real chat message via POST /api/chat — verify system still responds."""
    result = TestResult("chat_continuity")
    start = time.time()

    if not requests:
        result.skipped = True
        result.passed = True
        result.details.append("Skipped: 'requests' package not installed.")
        result.duration_ms = (time.time() - start) * 1000
        return result

    try:
        resp = requests.post(
            f"{api_url}/api/chat",
            json={
                "messages": [{"role": "user", "content": "Say hello in one word."}],
                "max_tokens": 30,
            },
            timeout=60,
        )
        result.details.append(f"Status: {resp.status_code}")

        if resp.status_code != 200:
            result.error = f"Chat API returned {resp.status_code}: {resp.text[:200]}"
            result.duration_ms = (time.time() - start) * 1000
            return result

        data = resp.json()
        content = data.get("message", {}).get("content", "")
        result.details.append(f"Response: '{content[:100]}'")

        if content.strip():
            result.passed = True
            result.details.append("Chat continuity PASSED — system responds normally.")
        else:
            result.error = "Chat returned empty content — system may be broken."

    except requests.ConnectionError:
        result.error = f"Cannot connect to {api_url}."
    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def phase_rollback_verification(api_url: str, expected_model: str, expected_provider: str) -> TestResult:
    """Phase 8: After a failed model switch, verify the original model is still active."""
    result = TestResult("rollback_verification")
    start = time.time()

    if not requests:
        result.skipped = True
        result.passed = True
        result.details.append("Skipped: 'requests' package not installed.")
        result.duration_ms = (time.time() - start) * 1000
        return result

    try:
        # Step 1: Record current active model
        status_resp = requests.get(f"{api_url}/api/plugins/status", timeout=10)
        if status_resp.status_code != 200:
            result.error = f"Cannot read plugin status: {status_resp.status_code}"
            result.duration_ms = (time.time() - start) * 1000
            return result

        before = status_resp.json()
        result.details.append(f"Before: model={before['active_model']}, provider={before['active_provider']}")

        # Step 2: Attempt to switch to a bad model (should fail)
        bad_resp = requests.post(
            f"{api_url}/api/plugins/add-model",
            json={
                "type": "open_source",
                "provider": "ollama",
                "model": "this-model-does-not-exist-12345:latest",
                "base_url": "http://localhost:11434",
            },
            timeout=30,
        )
        bad_data = bad_resp.json()
        result.details.append(f"Bad switch result: success={bad_data.get('success')}")

        if bad_data.get("success") is True:
            result.details.append("WARNING: Bad model switch unexpectedly succeeded!")
            # Even if it somehow succeeded, check if we can still chat

        # Step 3: Check that the original model is still active
        after_resp = requests.get(f"{api_url}/api/plugins/status", timeout=10)
        after = after_resp.json()
        result.details.append(f"After: model={after['active_model']}, provider={after['active_provider']}")

        if after["active_model"] == expected_model and after["active_provider"] == expected_provider:
            result.passed = True
            result.details.append(f"Rollback PASSED — original model '{expected_model}' still active.")
        else:
            result.error = (
                f"Rollback FAILED! Expected model='{expected_model}' provider='{expected_provider}', "
                f"but got model='{after['active_model']}' provider='{after['active_provider']}'"
            )

    except requests.ConnectionError:
        result.error = f"Cannot connect to {api_url}."
    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


# ────────────── Main Test Runner ──────────────


async def test_model_integration(
    provider_name: str,
    model_name: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    api_url: str = "http://localhost:8000",
) -> Dict[str, Any]:
    """
    Run all integration test phases for a model.
    Phases 1-4: Direct provider tests (connectivity, tools, streaming, system prompt).
    Phases 5-8: API-based safety tests (invalid model, bad provider, chat continuity, rollback).
    Returns structured JSON with per-phase results.
    """
    total_phases = 8
    logger.info(f"━━━ Model Integration Test: {provider_name}:{model_name} ━━━")

    # Save original env for cleanup
    original_env: Dict[str, Optional[str]] = {}

    try:
        # Set up environment
        if api_key:
            key_map = {
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "gemini": "GOOGLE_API_KEY",
            }
            env_key = key_map.get(provider_name)
            if env_key:
                original_env[env_key] = os.environ.get(env_key)
                os.environ[env_key] = api_key

        if base_url and provider_name == "ollama":
            original_env["OLLAMA_BASE_URL"] = os.environ.get("OLLAMA_BASE_URL")
            os.environ["OLLAMA_BASE_URL"] = base_url

        # Clear cached provider to pick up new env
        if provider_name in ProviderFactory._instances:
            del ProviderFactory._instances[provider_name]

        provider = ProviderFactory.get_provider(provider_name)

        # ── Run phases sequentially ──
        results: List[TestResult] = []

        # Phase 1: Connectivity (MUST pass)
        logger.info(f"Phase 1/{total_phases}: Connectivity...")
        r1 = await phase_connectivity(provider, model_name)
        results.append(r1)
        logger.info(f"  → {'PASS' if r1.passed else 'FAIL'} ({r1.duration_ms:.0f}ms)")

        if not r1.passed:
            logger.error("Connectivity failed — aborting remaining phases.")
            return _build_output(results, aborted=True)

        # Phase 2: Tool Calling
        logger.info(f"Phase 2/{total_phases}: Tool Calling...")
        r2 = await phase_tool_calling(provider, model_name)
        results.append(r2)
        logger.info(f"  → {'PASS' if r2.passed else ('SKIP' if r2.skipped else 'FAIL')} ({r2.duration_ms:.0f}ms)")

        # Phase 3: Streaming
        logger.info(f"Phase 3/{total_phases}: Streaming...")
        r3 = await phase_streaming(provider, model_name)
        results.append(r3)
        logger.info(f"  → {'PASS' if r3.passed else 'FAIL'} ({r3.duration_ms:.0f}ms)")

        # Phase 4: System Prompt
        logger.info(f"Phase 4/{total_phases}: System Prompt Compliance...")
        r4 = await phase_system_prompt(provider, model_name)
        results.append(r4)
        logger.info(f"  → {'PASS' if r4.passed else 'FAIL'} ({r4.duration_ms:.0f}ms)")

        # ── API-Based Safety Tests (Phases 5-8) ──
        logger.info(f"Phase 5/{total_phases}: Invalid Model Rejection...")
        r5 = phase_invalid_model_rejection(api_url)
        results.append(r5)
        logger.info(f"  → {'PASS' if r5.passed else ('SKIP' if r5.skipped else 'FAIL')} ({r5.duration_ms:.0f}ms)")

        logger.info(f"Phase 6/{total_phases}: Bad Provider Rejection...")
        r6 = phase_bad_provider_rejection(api_url)
        results.append(r6)
        logger.info(f"  → {'PASS' if r6.passed else ('SKIP' if r6.skipped else 'FAIL')} ({r6.duration_ms:.0f}ms)")

        logger.info(f"Phase 7/{total_phases}: Chat Continuity...")
        r7 = phase_chat_continuity(api_url)
        results.append(r7)
        logger.info(f"  → {'PASS' if r7.passed else ('SKIP' if r7.skipped else 'FAIL')} ({r7.duration_ms:.0f}ms)")

        logger.info(f"Phase 8/{total_phases}: Rollback Verification...")
        r8 = phase_rollback_verification(api_url, model_name, provider_name)
        results.append(r8)
        logger.info(f"  → {'PASS' if r8.passed else ('SKIP' if r8.skipped else 'FAIL')} ({r8.duration_ms:.0f}ms)")

        return _build_output(results)

    except Exception as e:
        logger.error(f"Test initialization failed: {e}")
        return {
            "success": False,
            "provider": provider_name,
            "model": model_name,
            "connectivity_ok": False,
            "tools_ok": False,
            "streaming_ok": False,
            "system_prompt_ok": False,
            "error": str(e),
            "phases": [],
        }

    finally:
        # Restore original env
        for key, original_val in original_env.items():
            if original_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_val


def _build_output(results: List[TestResult], aborted: bool = False) -> Dict[str, Any]:
    """Build the structured output dict from test results."""
    phases = [r.to_dict() for r in results]

    connectivity_ok = any(r.phase == "connectivity" and r.passed for r in results)
    tools_ok = any(r.phase == "tool_calling" and r.passed for r in results)
    streaming_ok = any(r.phase == "streaming" and r.passed for r in results)
    system_prompt_ok = any(r.phase == "system_prompt" and r.passed for r in results)
    invalid_model_ok = any(r.phase == "invalid_model_rejection" and r.passed for r in results)
    bad_provider_ok = any(r.phase == "bad_provider_rejection" and r.passed for r in results)
    chat_continuity_ok = any(r.phase == "chat_continuity" and r.passed for r in results)
    rollback_ok = any(r.phase == "rollback_verification" and r.passed for r in results)

    # Overall success = connectivity must pass; others are informational
    success = connectivity_ok and not aborted

    total_ms = sum(r.duration_ms for r in results)

    return {
        "success": success,
        "connectivity_ok": connectivity_ok,
        "tools_ok": tools_ok,
        "streaming_ok": streaming_ok,
        "system_prompt_ok": system_prompt_ok,
        "invalid_model_ok": invalid_model_ok,
        "bad_provider_ok": bad_provider_ok,
        "chat_continuity_ok": chat_continuity_ok,
        "rollback_ok": rollback_ok,
        "total_duration_ms": round(total_ms, 1),
        "aborted": aborted,
        "phases": phases,
        "details": [d for r in results for d in r.details],
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Model Integration Test — 8-phase validation")
    parser.add_argument("provider", help="Provider name (e.g. ollama, openai, anthropic, gemini)")
    parser.add_argument("model", help="Model name (e.g. qwen2.5:14b-instruct, gpt-4o-mini)")
    parser.add_argument("api_key", nargs="?", default=None, help="API key for paid providers")
    parser.add_argument("base_url", nargs="?", default=None, help="Custom base URL (e.g. for Ollama)")
    parser.add_argument("--api-url", default="http://localhost:8000", help="Backend API URL for safety tests (default: http://localhost:8000)")

    args = parser.parse_args()

    output = asyncio.run(test_model_integration(
        args.provider,
        args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        api_url=args.api_url,
    ))

    print()
    print("━━━ RESULTS ━━━")
    print(json.dumps(output, indent=2))

    sys.exit(0 if output["success"] else 1)
