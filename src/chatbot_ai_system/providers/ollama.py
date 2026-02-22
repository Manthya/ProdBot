"""Ollama LLM provider for local model inference."""

import logging
import time
from typing import AsyncGenerator, List, Optional

import httpx

from chatbot_ai_system.config import get_settings
from chatbot_ai_system.models.schemas import (
    ChatMessage,
    ChatResponse,
    MessageRole,
    StreamChunk,
    ToolCall,
    ToolCallFunction,
    UsageInfo,
)
from chatbot_ai_system.observability.metrics import (
    LLM_REQUEST_DURATION_SECONDS,
    LLM_REQUESTS_TOTAL,
    LLM_TOKENS_TOTAL,
    LLM_TTFT_SECONDS,
)
from chatbot_ai_system.config.settings_manager import settings_manager

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Ollama provider for local LLM models.

    Ollama allows running open source models locally including:
    - Llama 2
    - Mistral
    - CodeLlama
    - And many more
    """

    provider_name = "ollama"

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
    ):
        """Initialize Ollama provider.

        Args:
            base_url: Ollama API base URL (default: http://localhost:11434)
            default_model: Default model to use (default: llama2)
        """
        settings = get_settings()
        self.base_url = base_url or settings.ollama_base_url
        self.default_model = default_model or settings.ollama_model
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _try_parse_tool_calls(self, content: str) -> Optional[List[ToolCall]]:
        """Try to parse tool calls from message content (for models that output raw JSON)."""
        import json
        import re

        try:
            # Look for JSON blocks
            # This regex matches a JSON object that has "name" and "arguments" keys
            # It's a simple heuristic and might need refinement
            # We look for something that starts with { and contains "name" and "arguments"

            # First, try to parse the entire content as a single JSON object
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    name = data.get("name") or data.get("function", {}).get("name")
                    args = (
                        data.get("arguments")
                        or data.get("parameters")
                        or data.get("function", {}).get("arguments", {})
                    )
                    if name:
                        return [
                            ToolCall(
                                function=ToolCallFunction(
                                    name=name,
                                    arguments=args,
                                )
                            )
                        ]
            except (json.JSONDecodeError, AttributeError):
                pass

            # Next, try to find a code block containing JSON
            code_block_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
            matches = re.findall(code_block_pattern, content, re.DOTALL)

            if not matches:
                # Try to find raw JSON object in the text if no code block
                # Non-greedy match for { ... }
                # We expect "name": "..." and "arguments" (or "parameters"): { ... }
                raw_pattern = (
                    r"(\{.*?\"name\"\s*:\s*\".*?\".*?\"(arguments|parameters)\"\s*:\s*\{.*?\}.*?\})"
                )
                matches = [m[0] for m in re.findall(raw_pattern, content, re.DOTALL)]

            tool_calls = []
            for match in matches:
                try:
                    data = json.loads(match)
                    # Support both OpenAI/Ollama format and simpler variations
                    name = data.get("name") or data.get("function", {}).get("name")
                    args = (
                        data.get("arguments")
                        or data.get("parameters")
                        or data.get("function", {}).get("arguments", {})
                    )

                    if name and name != "null":
                        tool_calls.append(
                            ToolCall(
                                function=ToolCallFunction(
                                    name=name,
                                    arguments=args,
                                )
                            )
                        )
                except json.JSONDecodeError:
                    continue

            return tool_calls if tool_calls else None

        except Exception as e:
            logger.warning(f"Error parsing tool calls from content: {e}")
            return None

    def _format_messages(self, messages: List[ChatMessage]) -> List[dict]:
        """Format messages for Ollama API, including multimodal images."""
        formatted = []
        for msg in messages:
            m = {"role": msg.role.value, "content": msg.content}

            # Phase 5.0: Include images from attachments for vision models
            if msg.attachments:
                images = [
                    a.base64_data for a in msg.attachments if a.type == "image" and a.base64_data
                ]
                if images:
                    m["images"] = images

            # For tool results, Ollama expects tool_call_id
            if msg.role == MessageRole.TOOL:
                if msg.tool_call_id:
                    m["tool_call_id"] = msg.tool_call_id
                else:
                    logger.warning(f"Tool message missing tool_call_id: {msg}")

            # For assistant messages with tool calls
            if msg.tool_calls:
                m["tool_calls"] = [
                    {
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                        "type": "function",
                    }
                    for tc in msg.tool_calls
                ]
                # Some models/APIs prefer content to be null or omitted if tool_calls present
                if not m["content"]:
                    del m["content"]

            formatted.append(m)
        return formatted

    async def complete(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        repeat_penalty: float = 1.1,
        tools: Optional[List[dict]] = None,
        **kwargs,
    ) -> ChatResponse:
        """Generate a completion using Ollama."""
        start_time = time.time()
        client = await self._get_client()
        if model is None:
            model = await settings_manager.get_setting("ollama_model")
        model = model or self.default_model

        payload = {
            "model": model,
            "messages": self._format_messages(messages),
            "stream": False,
            "options": {
                "temperature": temperature,
                "repeat_penalty": repeat_penalty,
            },
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        if tools:
            payload["tools"] = tools

        try:
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

            latency_ms = (time.time() - start_time) * 1000

            # Record metrics
            status = "success"
            LLM_REQUESTS_TOTAL.labels(model=model, provider=self.provider_name, status=status).inc()
            LLM_REQUEST_DURATION_SECONDS.labels(model=model, provider=self.provider_name).observe(
                time.time() - start_time
            )

            prompt_tokens = data.get("prompt_eval_count", 0)
            completion_tokens = data.get("eval_count", 0)
            LLM_TOKENS_TOTAL.labels(model=model, provider=self.provider_name, type="prompt").inc(
                prompt_tokens
            )
            LLM_TOKENS_TOTAL.labels(
                model=model, provider=self.provider_name, type="completion"
            ).inc(completion_tokens)

            message_data = data.get("message", {})
            content = message_data.get("content", "")
            tool_calls_data = message_data.get("tool_calls", [])

            tool_calls = None
            if tool_calls_data:
                tool_calls = []
                for tc in tool_calls_data:
                    function_data = tc.get("function", {})
                    tool_calls.append(
                        ToolCall(
                            id=tc.get("id") or str(uuid.uuid4()),
                            function=ToolCallFunction(
                                name=function_data.get("name"),
                                arguments=function_data.get("arguments", {}),
                            ),
                        )
                    )

            # Fallback: Try to parse tool calls from content if none returned by API
            if not tool_calls and content:
                tool_calls = self._try_parse_tool_calls(content)

            if tool_calls:
                logger.info(f"Ollama tool calls found: {[tc.function.name for tc in tool_calls]}")

            if content:
                logger.info(f"Ollama response content: {content[:200]}...")

            return ChatResponse(
                message=ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=content,
                    tool_calls=tool_calls,
                ),
                usage=UsageInfo(
                    prompt_tokens=data.get("prompt_eval_count", 0),
                    completion_tokens=data.get("eval_count", 0),
                    total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
                ),
                model=model,
                provider=self.provider_name,
                latency_ms=latency_ms,
            )

        except httpx.HTTPError as e:
            logger.error(f"Ollama API error: {e}")
            LLM_REQUESTS_TOTAL.labels(
                model=model, provider=self.provider_name, status="error"
            ).inc()
            raise RuntimeError(f"Failed to get completion from Ollama: {e}")

    async def stream(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        repeat_penalty: float = 1.1,
        tools: Optional[List[dict]] = None,
        **kwargs,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream a completion using Ollama."""
        if model is None:
            model = await settings_manager.get_setting("ollama_model")
        model = model or self.default_model
        url = f"{self.base_url}/api/chat"

        payload = {
            "model": model,
            "messages": self._format_messages(messages),
            "stream": True,
            "options": {
                "temperature": temperature,
                "repeat_penalty": repeat_penalty,
            },
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        if tools:
            payload["tools"] = tools

        start_time = time.time()
        first_token_received = False

        try:
            # Use a fresh client for streaming to avoid connection issues
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            import json

                            data = json.loads(line)

                            if not first_token_received:
                                ttft = time.time() - start_time
                                LLM_TTFT_SECONDS.labels(
                                    model=model, provider=self.provider_name
                                ).observe(ttft)
                                first_token_received = True

                            message_data = data.get("message", {})
                            content = message_data.get("content", "")
                            done = data.get("done", False)

                            # Extract tool calls if present
                            tool_calls = None
                            tool_calls_data = message_data.get("tool_calls")
                            if tool_calls_data:
                                tool_calls = []
                                for tc in tool_calls_data:
                                    function_data = tc.get("function", {})
                                    tool_calls.append(
                                        ToolCall(
                                            id=tc.get("id") or str(uuid.uuid4()),
                                            function=ToolCallFunction(
                                                name=function_data.get("name"),
                                                arguments=function_data.get("arguments", {}),
                                            ),
                                        )
                                    )

                            if content or done or tool_calls:
                                usage = None
                                if done:
                                    usage = UsageInfo(
                                        prompt_tokens=data.get("prompt_eval_count", 0),
                                        completion_tokens=data.get("eval_count", 0),
                                        total_tokens=data.get("prompt_eval_count", 0)
                                        + data.get("eval_count", 0),
                                    )
                                    # Record metrics on completion
                                    LLM_REQUESTS_TOTAL.labels(
                                        model=model, provider=self.provider_name, status="success"
                                    ).inc()
                                    LLM_REQUEST_DURATION_SECONDS.labels(
                                        model=model, provider=self.provider_name
                                    ).observe(time.time() - start_time)
                                    LLM_TOKENS_TOTAL.labels(
                                        model=model, provider=self.provider_name, type="prompt"
                                    ).inc(usage.prompt_tokens)
                                    LLM_TOKENS_TOTAL.labels(
                                        model=model, provider=self.provider_name, type="completion"
                                    ).inc(usage.completion_tokens)

                                yield StreamChunk(
                                    content=content, done=done, tool_calls=tool_calls, usage=usage
                                )

        except httpx.HTTPError as e:
            logger.error(f"Ollama streaming error: {e}")
            LLM_REQUESTS_TOTAL.labels(
                model=model, provider=self.provider_name, status="error"
            ).inc()
            raise RuntimeError(f"Failed to stream from Ollama: {e}")

    async def health_check(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Ollama health check failed: {e}")
            return False

    def get_available_models(self) -> List[str]:
        """Get list of commonly available Ollama models."""
        # This returns common models; actual availability depends on what's pulled
        return [
            "llama2",
            "llama2:13b",
            "mistral",
            "codellama",
            "phi",
            "neural-chat",
            "starling-lm",
        ]

    async def list_local_models(self) -> List[str]:
        """Get list of models actually available locally."""
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except Exception as e:
            logger.warning(f"Failed to list Ollama models: {e}")
            return []
