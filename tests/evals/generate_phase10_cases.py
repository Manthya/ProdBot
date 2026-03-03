import csv
from pathlib import Path

CASE_FILE = Path(__file__).parent / "phase10_cases.csv"


def add_case(rows, *, test_id, query, phase, tool_required, domains, cap, tags, tool_name="", router_override="", stream_tool_calls="one", tool_behavior="ok", expect_verification=False, verify_outcome="", status_contains=""):
    rows.append(
        {
            "test_id": test_id,
            "query": query,
            "expected_phase": phase,
            "expected_tool_required": "true" if tool_required else "false",
            "expected_domains": ",".join(domains) if domains else "",
            "expected_tool_call_cap": str(cap),
            "router_override": router_override,
            "stream_tool_calls": stream_tool_calls,
            "tool_behavior": tool_behavior,
            "expected_verification": "true" if expect_verification else "false",
            "verify_outcome": verify_outcome,
            "expected_status_contains": status_contains,
            "expected_tool_name": tool_name,
            "coverage_tags": "|".join(tags) if tags else "",
        }
    )


rows = []
idx = 1

# General (30)
GENERAL_PROMPTS = [
    "Hello",
    "Hi there",
    "How are you?",
    "Thanks",
    "What is Python?",
    "Explain git in simple terms",
    "What is 2+2?",
    "Tell me a joke",
    "Summarize the idea of recursion",
    "What is a database?",
    "How does HTTP work?",
    "What is JSON?",
    "Define machine learning",
    "Explain REST APIs",
    "What is unit testing?",
    "Why use version control?",
    "Give me a short poem",
    "What is a monorepo?",
    "Explain TCP vs UDP",
    "What is a cache?",
    "Explain Docker in one paragraph",
    "What is concurrency?",
    "What is OAuth?",
    "What is load balancing?",
    "Explain hashing",
    "Explain logging levels",
    "What is latency?",
    "What is a CDN?",
    "Explain TLS",
    "What is an ORM?",
]
for prompt in GENERAL_PROMPTS:
    add_case(
        rows,
        test_id=f"G-{idx:03d}",
        query=prompt,
        phase="GENERAL",
        tool_required=False,
        domains=[],
        cap=0,
        tags=["phase:general", "domain:none"],
    )
    idx += 1

# Time (12)
TIME_PROMPTS = [
    ("What time is it right now?", "current_time"),
    ("Current time in Tokyo", "current_time"),
    ("UTC time please", "current_time"),
    ("Timestamp now please", "get_timestamp"),
    ("time now", "current_time"),
    ("what's the time", "current_time"),
    ("time in Paris", "current_time"),
    ("timezone for PST?", "current_time"),
    ("what time is it in New York", "current_time"),
    ("current time UTC", "current_time"),
    ("timestamp", "get_timestamp"),
    ("what time is it", "current_time"),
]
for prompt, tool_name in TIME_PROMPTS:
    add_case(
        rows,
        test_id=f"T-{idx:03d}",
        query=prompt,
        phase="MEDIUM",
        tool_required=True,
        domains=["time"],
        cap=1,
        tags=["phase:medium", "domain:time", "deterministic"],
        tool_name=tool_name,
    )
    idx += 1

# Git (10)
GIT_PROMPTS = [
    ("git status", "git_status"),
    ("Show me git log", "git_log"),
    ("git branch", "git_branch"),
    ("git diff", "git_diff"),
    ("git checkout main", "git_checkout"),
    ("show git status", "git_status"),
    ("git commit history", "git_log"),
    ("git status for this repo", "git_status"),
    ("show git branches", "git_branch"),
    ("git diff last commit", "git_diff"),
]
for prompt, tool_name in GIT_PROMPTS:
    add_case(
        rows,
        test_id=f"GIT-{idx:03d}",
        query=prompt,
        phase="MEDIUM",
        tool_required=True,
        domains=["git"],
        cap=1,
        tags=["phase:medium", "domain:git"],
        tool_name=tool_name,
    )
    idx += 1

# Fetch (12)
FETCH_PROMPTS = [
    ("Open https://example.com", "fetch_html"),
    ("Fetch https://example.com", "fetch_html"),
    ("Browse https://example.com", "fetch_html"),
    ("Search the web for cats", "web_search"),
    ("Look up the Eiffel Tower online", "web_search"),
    ("Find online info about pandas", "web_search"),
    ("Open https://example.com/page", "fetch_html"),
    ("Download https://example.com/file", "fetch_html"),
    ("Search the web for latest Python version", "web_search"),
    ("fetch https://example.com/docs", "fetch_html"),
    ("Open https://example.com and summarize", "fetch_html"),
    ("search the web for climate data", "web_search"),
]
for prompt, tool_name in FETCH_PROMPTS:
    add_case(
        rows,
        test_id=f"F-{idx:03d}",
        query=prompt,
        phase="MEDIUM",
        tool_required=True,
        domains=["fetch"],
        cap=1,
        tags=["phase:medium", "domain:fetch", "network"],
        tool_name=tool_name,
    )
    idx += 1

# Filesystem (16)
FS_PROMPTS = [
    ("List files in this folder", "directory_tree", "ok", False),
    ("Read ./README.md", "read_file", "ok", False),
    ("Open docs/phase10.0.md", "read_file", "ok", False),
    ("Show package.json", "read_file", "ok", False),
    ("List directory", "directory_tree", "ok", False),
    ("Create file notes.txt with hello", "write_file", "ok", True),
    ("Write a note to notes.txt", "write_file", "ok", True),
    ("Delete file ./tmp.txt", "delete_file", "error", True),
    ("Remove ./tmp.txt", "delete_file", "error", True),
    ("Update ./README.md", "write_file", "ok", True),
    ("Read file /etc/hosts", "read_file", "ok", False),
    ("List files here", "directory_tree", "ok", False),
    ("Show me the directory tree", "directory_tree", "ok", False),
    ("Open ./pyproject.toml", "read_file", "ok", False),
    ("Read tests/evals/phase10_cases.csv", "read_file", "ok", False),
    ("Show contents of docs/phase10.0_testing.md", "read_file", "ok", False),
]
for prompt, tool_name, behavior, side_effect in FS_PROMPTS:
    add_case(
        rows,
        test_id=f"FS-{idx:03d}",
        query=prompt,
        phase="MEDIUM",
        tool_required=True,
        domains=["filesystem"],
        cap=1,
        tags=["phase:medium", "domain:filesystem"] + (["side_effect"] if side_effect else []),
        tool_name=tool_name,
        tool_behavior=behavior,
        expect_verification=side_effect,
        verify_outcome="fail" if behavior == "error" else "pass" if side_effect else "",
    )
    idx += 1

# SQLite (12)
SQL_PROMPTS = [
    ("select * from users", "query", "ok", False),
    ("create table test(id int)", "create_table", "ok", True),
    ("insert into users values(1)", "insert_record", "ok", True),
    ("update users set name='a'", "execute", "ok", True),
    ("delete from users where id=1", "execute", "ok", True),
    ("drop table users", "drop_table", "error", True),
    ("select count(*) from users", "query", "ok", False),
    ("create table items(id int)", "create_table", "ok", True),
    ("insert into items values(2)", "insert_record", "ok", True),
    ("select * from items", "query", "ok", False),
    ("update items set id=3", "execute", "ok", True),
    ("delete from items", "execute", "ok", True),
]
for prompt, tool_name, behavior, side_effect in SQL_PROMPTS:
    add_case(
        rows,
        test_id=f"SQL-{idx:03d}",
        query=prompt,
        phase="MEDIUM",
        tool_required=True,
        domains=["sqlite"],
        cap=1,
        tags=["phase:medium", "domain:sqlite"] + (["side_effect"] if side_effect else []),
        tool_name=tool_name,
        tool_behavior=behavior,
        expect_verification=side_effect,
        verify_outcome="fail" if behavior == "error" else "pass" if side_effect else "",
    )
    idx += 1

# Complex (20)
COMPLEX_PROMPTS = [
    "Compare file A and file B",
    "Read README and summarize",
    "Find the bug in src and summarize",
    "Open https://example.com and summarize",
    "Search the web for pandas then summarize",
    "Read docs/phase10.0.md and extract key changes",
    "List files and then show git status",
    "Fetch https://example.com then summarize",
    "Read file X and compare to file Y",
    "Search web for cats and list results",
    "Read README.md and check for TODO",
    "Open docs and summarize differences",
    "Fetch URL and extract title",
    "Search for API docs then summarize",
    "List directory and then read README",
    "Check git status and then show diff",
    "Compare two files and explain changes",
    "Search online then update local notes",
    "Read error log and find root cause",
    "Analyze code and suggest fixes",
]
for prompt in COMPLEX_PROMPTS:
    add_case(
        rows,
        test_id=f"C-{idx:03d}",
        query=prompt,
        phase="COMPLEX",
        tool_required=True,
        domains=["filesystem"],
        cap=2,
        tags=["phase:complex", "multi_step"],
        router_override='{"phase":"COMPLEX","tool_required":true,"tool_domains":["filesystem"],"expected_tool_calls":2,"confidence":0.8,"need_clarification":false}',
    )
    idx += 1

# Router edge cases (mock-only)
add_case(
    rows,
    test_id=f"E-{idx:03d}",
    query="Router malformed JSON test",
    phase="MEDIUM",
    tool_required=True,
    domains=["fetch"],
    cap=1,
    tags=["phase:medium", "mock_only"],
    router_override="MALFORMED",
    tool_name="fetch_html",
)
idx += 1
add_case(
    rows,
    test_id=f"E-{idx:03d}",
    query="Router low confidence fallback",
    phase="GENERAL",
    tool_required=False,
    domains=[],
    cap=0,
    tags=["phase:general", "mock_only"],
    router_override="LOWCONF",
    stream_tool_calls="none",
)
idx += 1
add_case(
    rows,
    test_id=f"E-{idx:03d}",
    query="Tool spam in medium",
    phase="MEDIUM",
    tool_required=True,
    domains=["filesystem"],
    cap=1,
    tags=["phase:medium", "mock_only"],
    stream_tool_calls="multi",
    tool_name="read_file",
)
idx += 1
add_case(
    rows,
    test_id=f"E-{idx:03d}",
    query="Forced retry tool selection",
    phase="MEDIUM",
    tool_required=True,
    domains=["filesystem"],
    cap=1,
    tags=["phase:medium", "mock_only"],
    stream_tool_calls="none",
    tool_name="read_file",
    status_contains="Rethinking",
)

CASE_FILE.parent.mkdir(parents=True, exist_ok=True)
fieldnames = [
    "test_id",
    "query",
    "expected_phase",
    "expected_tool_required",
    "expected_domains",
    "expected_tool_call_cap",
    "router_override",
    "stream_tool_calls",
    "tool_behavior",
    "expected_verification",
    "verify_outcome",
    "expected_status_contains",
    "expected_tool_name",
    "coverage_tags",
]
with CASE_FILE.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} cases to {CASE_FILE}")
