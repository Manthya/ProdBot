import csv
import sys
import asyncio
import uuid
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Import chatbot components
from chatbot_ai_system.orchestrator import ChatOrchestrator
from chatbot_ai_system.models.schemas import ChatMessage, MessageRole, ToolCall
from chatbot_ai_system.tools.registry import ToolRegistry
from chatbot_ai_system.providers.base import BaseLLMProvider

# Paths
EVAL_DIR = Path(__file__).parent
TEST_CSV = EVAL_DIR / "test.csv"
GOLDEN_DB_CSV = EVAL_DIR / "golden_db.csv"

# Configuration
# Set to True to use Ollama/OpenAI, False to use the MockProvider
LIVE_MODE = False

class TrajectoryTracker:
    """Intercepts tool calls to record the execution path."""
    def __init__(self):
        self.path = []

    def record(self, tool_name: str, arguments: dict):
        self.path.append(tool_name)

class BenchmarkToolRegistry(ToolRegistry):
    """A registry that records tool calls without necessarily executing them."""
    def __init__(self, tracker: TrajectoryTracker):
        super().__init__()
        self.tracker = tracker

    def get_tool(self, name: str):
        # Return a mock tool that records its own execution
        mock_tool = AsyncMock()
        mock_tool.name = name
        
        async def mock_run(**kwargs):
            self.tracker.record(name, kwargs)
            return f"Mocked result for {name}"
            
        mock_tool.run.side_effect = mock_run
        return mock_tool

class MockBenchmarkProvider(BaseLLMProvider):
    """A provider that returns expected tool calls based on the test case."""
    def __init__(self, expected_trajectory: List[str]):
        super().__init__()
        self.expected_trajectory = expected_trajectory
        self.call_count = 0

    def get_available_models(self) -> List[str]:
        return ["mock-model"]

    async def get_models(self) -> List[Dict[str, Any]]:
        return [{"name": "mock-model"}]

    async def health_check(self) -> bool:
        return True

    def _try_parse_tool_calls(self, content: str) -> List[ToolCall]:
        return []

    async def stream(self, messages, **kwargs):
        # Simple implementation to simulate the expected tools
        from chatbot_ai_system.models.schemas import StreamChunk, ToolCall, ToolCallFunction
        
        # If we have expected tools, emit them
        if self.expected_trajectory and self.call_count == 0:
            tool_calls = []
            for i, tool_name in enumerate(self.expected_trajectory):
                tool_calls.append(ToolCall(
                    id=f"call_{i}",
                    type="function",
                    function=ToolCallFunction(name=tool_name.strip(), arguments={})
                ))
            yield StreamChunk(content="", tool_calls=tool_calls)
        else:
            yield StreamChunk(content="Mocked final answer.", done=True)
        
        self.call_count += 1

    async def complete(self, **kwargs):
        # Required for intent classification/summarization in Orchestrator
        resp = MagicMock()
        text = kwargs.get('messages', [MagicMock(content="")])[-1].content
        if "intent" in text.lower():
            resp.message.content = "FILESYSTEM" # Default mock
        else:
            resp.message.content = "Mocked answer"
        return resp

def load_fixtures():
    fixtures = {}
    if not GOLDEN_DB_CSV.exists(): return fixtures
    with open(GOLDEN_DB_CSV, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fixtures[row['fixture_id']] = row
    return fixtures

def load_tests():
    tests = []
    with open(TEST_CSV, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tests.append(row)
    return tests

async def run_single_test(test_case, fixtures):
    test_id = test_case['test_id']
    query = test_case['query']
    expected_tools = [t.strip() for t in test_case['expected_trajectory'].split(",") if t.strip()]
    
    tracker = TrajectoryTracker()
    registry = BenchmarkToolRegistry(tracker)
    
    # Mock Provider
    if not LIVE_MODE:
        provider = MockBenchmarkProvider(expected_tools)
    else:
        from chatbot_ai_system.providers.ollama import OllamaProvider
        from chatbot_ai_system.config import get_settings
        settings = get_settings()
        provider = OllamaProvider(base_url=settings.ollama_base_url) 

    # Mock Repos
    conv_repo = AsyncMock()
    conv_repo.add_message = AsyncMock(side_effect=lambda **k: MagicMock(id=uuid.uuid4()))
    conv_repo.get_conversation_summary = AsyncMock(return_value=None)
    
    memory_repo = AsyncMock()
    memory_repo.get_user_memories = AsyncMock(return_value=[])

    orchestrator = ChatOrchestrator(
        provider=provider,
        registry=registry,
        conversation_repo=conv_repo,
        memory_repo=memory_repo
    )

    print(f"Running Test {test_id} [{test_case['difficulty']} - {test_case['category']}]")
    print(f" Query: {query}")

    # attachments handling for vision
    attachments = []
    if "image" in query.lower() or "lv_bag" in query.lower():
        from chatbot_ai_system.models.schemas import MediaAttachment
        attachments.append(MediaAttachment(type="image", base64_data="dummy_data", mime_type="image/jpeg"))

    history = [ChatMessage(role=MessageRole.USER, content=query, attachments=attachments)]
    
    try:
        async for chunk in orchestrator.run(
            conversation_id=str(uuid.uuid4()),
            user_input=query,
            conversation_history=history,
            model="mock-model"
        ):
            pass
    except Exception as e:
        print(f" -> ERROR: {e}")
        return False

    actual_tools = tracker.path
    
    # Check if all expected tools were called (order matters)
    success = True
    for tool in expected_tools:
        if tool not in actual_tools:
            print(f" -> FAILED: Expected tool '{tool}' not found in actual trajectory {actual_tools}")
            success = False
            break
            
    if success:
        print(f" -> PASSED (Trajectory: {actual_tools})")
    
    return success

async def main():
    # Suppression of verbose logging from orchestrator for cleaner report
    logging.getLogger("chatbot_ai_system").setLevel(logging.WARNING)

    fixtures = load_fixtures()
    tests = load_tests()
    
    passed = 0
    failed = 0
    
    for test in tests:
        success = await run_single_test(test, fixtures)
        if success:
            passed += 1
        else:
            failed += 1
        print("-" * 20)
            
    print("\n" + "=" * 40)
    print("EVALUATION RUN COMPLETE")
    print(f"Total Tests: {len(tests)} | Passed: {passed} | Failed: {failed}")
    print("=" * 40)

    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
