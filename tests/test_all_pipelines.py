#!/usr/bin/env python3
"""
Comprehensive end-to-end pipeline test for the Chatbot AI System.
Tests all conversation pipelines: REST, WebSocket, CRUD, error handling.

Usage: PYTHONPATH=src .venv/bin/python scripts/test_all_pipelines.py
"""

import asyncio
import json
import time
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

API_BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

results = []

def log_result(test_name, passed, duration, detail=""):
    status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    print(f"  {status}  {test_name} ({duration:.2f}s)")
    if detail:
        # Truncate long details
        preview = detail[:200].replace('\n', ' ')
        if len(detail) > 200:
            preview += "..."
        print(f"         → {preview}")
    results.append({"test": test_name, "passed": passed, "duration": duration, "detail": detail})


async def run_all_tests():
    import httpx

    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  Chatbot AI System — Pipeline Tests{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")

    # ═══════════════════════════════════════════
    # T1: Health Check
    # ═══════════════════════════════════════════
    print(f"{BOLD}[T1] Health Check{RESET}")
    try:
        start = time.time()
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_BASE}/health", timeout=10)
        dur = time.time() - start
        data = resp.json()
        passed = resp.status_code == 200 and data.get("status") in ("healthy", "degraded")
        log_result("Health Check", passed, dur, f"status={data.get('status')}, providers={data.get('providers')}")
    except Exception as e:
        log_result("Health Check", False, 0, str(e))

    # ═══════════════════════════════════════════
    # T2: Simple Greeting (GENERAL intent, no tools)
    # ═══════════════════════════════════════════
    print(f"\n{BOLD}[T2] Simple Greeting (REST /api/chat){RESET}")
    conversation_id = None
    try:
        start = time.time()
        payload = {
            "messages": [{"role": "user", "content": "Hello! How are you today?"}],
            "model": "qwen2.5:14b-instruct",
            "provider": "ollama"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{API_BASE}/api/chat", json=payload, timeout=120)
        dur = time.time() - start
        data = resp.json()

        if resp.status_code == 200:
            content = data.get("message", {}).get("content", "")
            tool_calls = data.get("message", {}).get("tool_calls")
            conversation_id = data.get("conversation_id")
            passed = bool(content) and len(content) > 5
            log_result("Simple Greeting", passed, dur, f"response='{content[:150]}'")
            if tool_calls:
                print(f"         {YELLOW}⚠ Unexpected tool calls: {tool_calls}{RESET}")
        else:
            log_result("Simple Greeting", False, dur, f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log_result("Simple Greeting", False, 0, str(e))

    # ═══════════════════════════════════════════
    # T3: Follow-up in Same Conversation (Memory)
    # ═══════════════════════════════════════════
    print(f"\n{BOLD}[T3] Conversation Memory (follow-up){RESET}")
    try:
        if not conversation_id:
            log_result("Conversation Memory", False, 0, "No conversation_id from T2")
        else:
            start = time.time()
            payload = {
                "messages": [{"role": "user", "content": "My name is TestUser. Remember that."}],
                "model": "qwen2.5:14b-instruct",
                "provider": "ollama",
                "conversation_id": conversation_id
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{API_BASE}/api/chat", json=payload, timeout=120)
            dur = time.time() - start
            data = resp.json()

            if resp.status_code == 200:
                content = data.get("message", {}).get("content", "")
                passed = bool(content) and len(content) > 3
                log_result("Memory: Set Name", passed, dur, f"response='{content[:150]}'")

                # Follow-up: ask for the name
                start2 = time.time()
                payload2 = {
                    "messages": [{"role": "user", "content": "What is my name?"}],
                    "model": "qwen2.5:14b-instruct",
                    "provider": "ollama",
                    "conversation_id": conversation_id
                }
                async with httpx.AsyncClient() as client:
                    resp2 = await client.post(f"{API_BASE}/api/chat", json=payload2, timeout=120)
                dur2 = time.time() - start2
                data2 = resp2.json()
                content2 = data2.get("message", {}).get("content", "")
                passed2 = "testuser" in content2.lower() or "test" in content2.lower()
                log_result("Memory: Recall Name", passed2, dur2, f"response='{content2[:150]}'")
            else:
                log_result("Conversation Memory", False, dur, f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log_result("Conversation Memory", False, 0, str(e))

    # ═══════════════════════════════════════════
    # T4: Tool-Requiring Query (FILESYSTEM)
    # ═══════════════════════════════════════════
    print(f"\n{BOLD}[T4] Tool Use — Filesystem Query{RESET}")
    try:
        start = time.time()
        payload = {
            "messages": [{"role": "user", "content": "List the files in the current directory"}],
            "model": "qwen2.5:14b-instruct",
            "provider": "ollama"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{API_BASE}/api/chat", json=payload, timeout=180)
        dur = time.time() - start
        data = resp.json()

        if resp.status_code == 200:
            content = data.get("message", {}).get("content", "")
            tool_calls = data.get("message", {}).get("tool_calls") or []
            passed = bool(content) and len(content) > 10
            detail = f"response='{content[:150]}'"
            if tool_calls:
                detail += f" | tools={[tc.get('function', {}).get('name', '?') for tc in tool_calls]}"
            log_result("Filesystem Query", passed, dur, detail)
        else:
            log_result("Filesystem Query", False, dur, f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log_result("Filesystem Query", False, 0, str(e))

    # ═══════════════════════════════════════════
    # T5: Complex Multi-Step Query (Agentic)
    # ═══════════════════════════════════════════
    print(f"\n{BOLD}[T5] Complex Agentic Query{RESET}")
    try:
        start = time.time()
        payload = {
            "messages": [{"role": "user", "content": "Check the git status of this project and tell me what branch I'm on"}],
            "model": "qwen2.5:14b-instruct",
            "provider": "ollama"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{API_BASE}/api/chat", json=payload, timeout=300)
        dur = time.time() - start
        data = resp.json()

        if resp.status_code == 200:
            content = data.get("message", {}).get("content", "")
            passed = bool(content) and len(content) > 10
            log_result("Agentic Query", passed, dur, f"response='{content[:200]}'")
        else:
            log_result("Agentic Query", False, dur, f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log_result("Agentic Query", False, 0, str(e))

    # ═══════════════════════════════════════════
    # T6: WebSocket Streaming
    # ═══════════════════════════════════════════
    print(f"\n{BOLD}[T6] WebSocket Streaming{RESET}")
    try:
        import websockets

        start = time.time()
        payload = {
            "messages": [{"role": "user", "content": "What is Python programming language?"}],
            "model": "qwen2.5:14b-instruct",
            "provider": "ollama"
        }

        full_content = ""
        got_done = False
        ws_conv_id = None
        chunk_count = 0

        async with websockets.connect(f"{WS_BASE}/api/chat/stream", close_timeout=5) as ws:
            await ws.send(json.dumps(payload))

            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=120)
                    chunk = json.loads(msg)
                    chunk_count += 1

                    if chunk.get("error"):
                        log_result("WebSocket Streaming", False, time.time() - start, f"Error: {chunk}")
                        break

                    content = chunk.get("content", "")
                    full_content += content

                    if chunk.get("conversation_id"):
                        ws_conv_id = chunk["conversation_id"]

                    if chunk.get("done"):
                        got_done = True
                        break
                except asyncio.TimeoutError:
                    log_result("WebSocket Streaming", False, time.time() - start, "Timeout waiting for WS response")
                    break

        dur = time.time() - start
        if got_done and full_content:
            passed = len(full_content) > 20 and chunk_count > 1
            log_result("WebSocket Streaming", passed, dur,
                       f"chunks={chunk_count}, content='{full_content[:150]}'")
        elif not got_done:
            log_result("WebSocket Streaming", False, dur, f"Never received done=true. Got {chunk_count} chunks, content='{full_content[:100]}'")
        else:
            log_result("WebSocket Streaming", False, dur, "Empty content received")

    except ImportError:
        log_result("WebSocket Streaming", False, 0, "websockets module not installed")
    except Exception as e:
        log_result("WebSocket Streaming", False, 0, str(e))

    # ═══════════════════════════════════════════
    # T7: Conversation CRUD
    # ═══════════════════════════════════════════
    print(f"\n{BOLD}[T7] Conversation CRUD{RESET}")
    try:
        # List conversations
        start = time.time()
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_BASE}/api/conversations", timeout=10)
        dur = time.time() - start
        if resp.status_code == 200:
            convs = resp.json()
            log_result("List Conversations", True, dur, f"Found {len(convs)} conversations")
        else:
            log_result("List Conversations", False, dur, f"HTTP {resp.status_code}")

        # Get conversation by ID (use the one from T2)
        if conversation_id:
            start = time.time()
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{API_BASE}/api/conversations/{conversation_id}", timeout=10)
            dur = time.time() - start
            if resp.status_code == 200:
                msgs = resp.json()
                log_result("Get Conversation", True, dur, f"Found {len(msgs)} messages in conversation")
            else:
                log_result("Get Conversation", False, dur, f"HTTP {resp.status_code}: {resp.text[:100]}")

        # Get non-existent conversation
        start = time.time()
        fake_id = "00000000-0000-0000-0000-999999999999"
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_BASE}/api/conversations/{fake_id}", timeout=10)
        dur = time.time() - start
        passed = resp.status_code == 404
        log_result("Get Missing Conversation (404)", passed, dur, f"HTTP {resp.status_code}")

    except Exception as e:
        log_result("Conversation CRUD", False, 0, str(e))

    # ═══════════════════════════════════════════
    # T8: Error Handling
    # ═══════════════════════════════════════════
    print(f"\n{BOLD}[T8] Error Handling{RESET}")
    try:
        # Empty messages
        start = time.time()
        payload = {
            "messages": [],
            "model": "qwen2.5:14b-instruct"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{API_BASE}/api/chat", json=payload, timeout=10)
        dur = time.time() - start
        passed = resp.status_code in (400, 422, 500)  # Should reject empty messages
        log_result("Empty Messages", passed, dur, f"HTTP {resp.status_code}: {resp.text[:100]}")

        # Invalid JSON
        start = time.time()
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{API_BASE}/api/chat", content="not json",
                                     headers={"Content-Type": "application/json"}, timeout=10)
        dur = time.time() - start
        passed = resp.status_code == 422
        log_result("Invalid JSON", passed, dur, f"HTTP {resp.status_code}")

        # Missing required fields
        start = time.time()
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{API_BASE}/api/chat", json={"model": "test"}, timeout=10)
        dur = time.time() - start
        passed = resp.status_code == 422
        log_result("Missing Fields", passed, dur, f"HTTP {resp.status_code}")

    except Exception as e:
        log_result("Error Handling", False, 0, str(e))

    # ═══════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  Summary{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    for r in results:
        status = f"{GREEN}✓{RESET}" if r["passed"] else f"{RED}✗{RESET}"
        print(f"  {status} {r['test']} ({r['duration']:.2f}s)")

    print(f"\n  {BOLD}Total: {total} | {GREEN}Passed: {passed}{RESET} | {RED}Failed: {failed}{RESET}")
    total_time = sum(r["duration"] for r in results)
    print(f"  Total time: {total_time:.2f}s\n")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
