"""
Dedicated MCP Server Management Test Script

Thoroughly validates the MCP server add/remove/update flow via the plugin API.
Tests listing, adding, duplicate protection, tool refresh, removal, and invalid configs.

Usage:
    python3 scripts/test_mcp_server_management.py [base_url]

Examples:
    python3 scripts/test_mcp_server_management.py
    python3 scripts/test_mcp_server_management.py http://localhost:8000
"""

import json
import sys
import time
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)


DEFAULT_API_URL = "http://localhost:8000"

# Test MCP server config (uses the harmless 'time' server)
TEST_SERVER = {
    "name": "__test_mcp_server__",
    "command": "npx",
    "args": ["-y", "@mcpcentral/mcp-time"],
    "env_vars": {},
    "required_env_vars": [],
}


class PhaseResult:
    """Result for a single test phase."""

    def __init__(self, phase: str, description: str):
        self.phase = phase
        self.description = description
        self.passed = False
        self.skipped = False
        self.details: List[str] = []
        self.error: Optional[str] = None
        self.duration_ms: float = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "description": self.description,
            "passed": self.passed,
            "skipped": self.skipped,
            "details": self.details,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 1),
        }


def phase_list_servers(api_url: str) -> PhaseResult:
    """Phase 1: GET /api/plugins/mcp-servers — verify the endpoint returns a valid list."""
    result = PhaseResult("list_servers", "List all configured MCP servers")
    start = time.time()

    try:
        resp = requests.get(f"{api_url}/api/plugins/mcp-servers", timeout=10)
        result.details.append(f"Status: {resp.status_code}")

        if resp.status_code != 200:
            result.error = f"Expected 200, got {resp.status_code}"
            result.duration_ms = (time.time() - start) * 1000
            return result

        data = resp.json()
        if not isinstance(data, list):
            result.error = f"Expected list, got {type(data).__name__}"
            result.duration_ms = (time.time() - start) * 1000
            return result

        result.details.append(f"Server count: {len(data)}")
        for srv in data[:5]:
            result.details.append(f"  - {srv.get('name', '?')} ({srv.get('command', '?')})")

        # Validate structure of each entry
        for srv in data:
            if "name" not in srv or "command" not in srv:
                result.error = f"Server entry missing required fields: {srv}"
                result.duration_ms = (time.time() - start) * 1000
                return result

        result.passed = True

    except requests.ConnectionError:
        result.error = f"Cannot connect to {api_url}. Is the server running?"
    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def phase_add_server(api_url: str) -> PhaseResult:
    """Phase 2: POST /api/plugins/mcp-servers — add a test MCP server."""
    result = PhaseResult("add_server", "Add a new MCP server configuration")
    start = time.time()

    try:
        resp = requests.post(
            f"{api_url}/api/plugins/mcp-servers",
            json=TEST_SERVER,
            timeout=15,
        )
        result.details.append(f"Status: {resp.status_code}")
        result.details.append(f"Response: {resp.text[:200]}")

        if resp.status_code == 200:
            data = resp.json()
            if data.get("name") == TEST_SERVER["name"]:
                result.passed = True
                result.details.append("Server added successfully.")
            else:
                result.error = f"Response name mismatch: {data.get('name')}"
        elif resp.status_code == 409:
            # Already exists from a previous run — clean it up first and retry
            result.details.append("Server already exists, attempting cleanup first...")
            del_resp = requests.delete(
                f"{api_url}/api/plugins/mcp-servers/{TEST_SERVER['name']}", timeout=10
            )
            result.details.append(f"Cleanup status: {del_resp.status_code}")

            # Retry add
            resp2 = requests.post(
                f"{api_url}/api/plugins/mcp-servers",
                json=TEST_SERVER,
                timeout=15,
            )
            if resp2.status_code == 200:
                result.passed = True
                result.details.append("Server added successfully after cleanup.")
            else:
                result.error = f"Retry failed with status {resp2.status_code}: {resp2.text[:100]}"
        else:
            result.error = f"Expected 200, got {resp.status_code}: {resp.text[:100]}"

    except requests.ConnectionError:
        result.error = f"Cannot connect to {api_url}."
    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def phase_verify_added(api_url: str) -> PhaseResult:
    """Phase 3: Verify the added server appears in the list and tools may have refreshed."""
    result = PhaseResult("verify_added", "Verify new server appears in list")
    start = time.time()

    try:
        resp = requests.get(f"{api_url}/api/plugins/mcp-servers", timeout=10)
        if resp.status_code != 200:
            result.error = f"List returned status {resp.status_code}"
            result.duration_ms = (time.time() - start) * 1000
            return result

        servers = resp.json()
        found = any(s.get("name") == TEST_SERVER["name"] for s in servers)

        if found:
            result.passed = True
            result.details.append(f"Server '{TEST_SERVER['name']}' found in list.")
        else:
            result.error = f"Server '{TEST_SERVER['name']}' NOT found in list."
            result.details.append(f"Server names in list: {[s.get('name') for s in servers]}")

        # Also check tool count from status
        status_resp = requests.get(f"{api_url}/api/plugins/status", timeout=10)
        if status_resp.status_code == 200:
            status = status_resp.json()
            result.details.append(f"Total tool count: {status.get('tool_count', '?')}")

    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def phase_remove_server(api_url: str) -> PhaseResult:
    """Phase 4: DELETE /api/plugins/mcp-servers/{name} — remove the test server."""
    result = PhaseResult("remove_server", "Remove the test MCP server")
    start = time.time()

    try:
        resp = requests.delete(
            f"{api_url}/api/plugins/mcp-servers/{TEST_SERVER['name']}", timeout=10
        )
        result.details.append(f"Status: {resp.status_code}")

        if resp.status_code == 200:
            result.passed = True
            result.details.append("Server removed successfully.")

            # Verify it's gone
            list_resp = requests.get(f"{api_url}/api/plugins/mcp-servers", timeout=10)
            if list_resp.status_code == 200:
                servers = list_resp.json()
                still_exists = any(s.get("name") == TEST_SERVER["name"] for s in servers)
                if still_exists:
                    result.passed = False
                    result.error = "Server still appears in list after deletion!"
                else:
                    result.details.append("Confirmed: server no longer in list.")
        else:
            result.error = f"Expected 200, got {resp.status_code}: {resp.text[:100]}"

    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def phase_duplicate_protection(api_url: str) -> PhaseResult:
    """Phase 5: Adding a server with an existing name should be rejected (409)."""
    result = PhaseResult("duplicate_protection", "Reject duplicate MCP server name")
    start = time.time()

    try:
        # First, add the test server
        resp1 = requests.post(
            f"{api_url}/api/plugins/mcp-servers",
            json=TEST_SERVER,
            timeout=15,
        )
        result.details.append(f"First add: status {resp1.status_code}")

        if resp1.status_code not in (200, 409):
            result.error = f"Initial add failed: {resp1.status_code}"
            result.duration_ms = (time.time() - start) * 1000
            return result

        # Now try to add again — should get 409
        resp2 = requests.post(
            f"{api_url}/api/plugins/mcp-servers",
            json=TEST_SERVER,
            timeout=15,
        )
        result.details.append(f"Duplicate add: status {resp2.status_code}")

        if resp2.status_code == 409:
            result.passed = True
            result.details.append("Duplicate correctly rejected with 409.")
        else:
            result.error = f"Expected 409 for duplicate, got {resp2.status_code}"

        # Cleanup
        requests.delete(
            f"{api_url}/api/plugins/mcp-servers/{TEST_SERVER['name']}", timeout=10
        )
        result.details.append("Cleanup: test server removed.")

    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def phase_invalid_config(api_url: str) -> PhaseResult:
    """Phase 6: Submitting invalid configs should return proper errors (422)."""
    result = PhaseResult("invalid_config", "Reject invalid MCP server configs")
    start = time.time()

    invalid_configs = [
        {"name": "bad1"},  # Missing 'command' and 'args'
        {"command": "npx", "args": ["-y", "something"]},  # Missing 'name'
        {},  # Completely empty
    ]

    all_rejected = True

    try:
        for i, config in enumerate(invalid_configs):
            resp = requests.post(
                f"{api_url}/api/plugins/mcp-servers",
                json=config,
                timeout=10,
            )
            result.details.append(f"Config {i + 1}: status {resp.status_code}")

            if resp.status_code == 422:
                result.details.append(f"  Correctly rejected with 422.")
            elif resp.status_code == 200:
                result.details.append(f"  WARNING: Invalid config was accepted!")
                all_rejected = False
                # Cleanup if accidentally added
                name = config.get("name")
                if name:
                    requests.delete(f"{api_url}/api/plugins/mcp-servers/{name}", timeout=10)
            else:
                result.details.append(f"  Status: {resp.status_code} (expected 422)")

        if all_rejected:
            result.passed = True
        else:
            result.error = "Some invalid configs were accepted instead of rejected."

    except Exception as e:
        result.error = str(e)

    result.duration_ms = (time.time() - start) * 1000
    return result


def run_all_tests(api_url: str) -> Dict[str, Any]:
    """Run all 6 phases and return structured results."""
    print(f"━━━ MCP Server Management Test — {api_url} ━━━\n")

    phases = [
        ("Phase 1/6", phase_list_servers),
        ("Phase 2/6", phase_add_server),
        ("Phase 3/6", phase_verify_added),
        ("Phase 4/6", phase_remove_server),
        ("Phase 5/6", phase_duplicate_protection),
        ("Phase 6/6", phase_invalid_config),
    ]

    results: List[PhaseResult] = []

    for label, phase_fn in phases:
        print(f"{label}: {phase_fn.__doc__.strip()[:60]}...")
        r = phase_fn(api_url)
        results.append(r)
        status = "PASS" if r.passed else ("SKIP" if r.skipped else "FAIL")
        print(f"  → {status} ({r.duration_ms:.0f}ms)")
        if r.error:
            print(f"  ✗ {r.error}")

        # If list fails, server is down — abort
        if r.phase == "list_servers" and not r.passed:
            print("\nServer unreachable — aborting remaining tests.")
            break

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    return {
        "success": all(r.passed for r in results),
        "passed": passed,
        "total": total,
        "total_duration_ms": round(sum(r.duration_ms for r in results), 1),
        "phases": [r.to_dict() for r in results],
    }


if __name__ == "__main__":
    api = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_API_URL

    output = run_all_tests(api)

    print()
    print("━━━ RESULTS ━━━")
    print(json.dumps(output, indent=2))

    sys.exit(0 if output["success"] else 1)
