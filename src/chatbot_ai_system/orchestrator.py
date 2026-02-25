"""
Chat Orchestrator Module.

This module implements the 9-phase architecture for handling chat requests,
including intent classification, tool scope reduction, and orchestrating
the interaction between the LLM and MCP tools.

Phase 5.5: Adds agentic orchestration for complex multi-step tasks.
- Combined classifier: INTENT + COMPLEXITY in one LLM call
- SIMPLE queries → fast one-shot path (unchanged)
- COMPLEX queries → Plan + ReAct agentic loop
"""

import asyncio
import json
import logging
import re
import time
import inspect
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import UUID

from chatbot_ai_system.config import get_settings
from chatbot_ai_system.database.redis import redis_client
from chatbot_ai_system.models.schemas import (
    ChatMessage,
    MessageRole,
    StreamChunk,
    ToolCall,
)
from chatbot_ai_system.observability.metrics import (
    INTENT_CLASSIFICATION_TOTAL,
    ORCHESTRATOR_REQUEST_DURATION_SECONDS,
    TOOL_EXECUTION_DURATION_SECONDS,
    TOOL_EXECUTION_TOTAL,
)
from chatbot_ai_system.providers.base import BaseLLMProvider
from chatbot_ai_system.services.agentic_engine import AgenticEngine
from chatbot_ai_system.services.embedding import EmbeddingService
from chatbot_ai_system.personal.constants import get_hitl_tool_names
from chatbot_ai_system.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Fix 2.3: Fast-path bypass for trivially simple queries
TRIVIAL_PATTERNS = re.compile(
    r'^(hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure|good|great|bye|'
    r'what is your name|who are you|how are you)[!?.\s]*$',
    re.IGNORECASE
)

# Guardrail: Prevent multi-MB tool payloads from stalling synthesis or bloating DB rows.
MAX_TOOL_RESULT_CHARS = 50_000
SIMPLE_LISTING_PHASE6_MAX_TOKENS = 220
SIMPLE_LISTING_SYNTHESIS_MAX_TOKENS = 260
FORCED_TOOL_CALL_MAX_TOKENS = 180
SIMPLE_LISTING_EXCLUDE_PATTERNS = [
    ".git",
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
]

SIMULATION_MARKERS = (
    "simulate",
    "simulated",
    "assuming",
    "assume",
    "if tool",
    "no tool provided",
    "no direct tool",
    "i don't have the capability",
    "i do not have the capability",
    "i can't access",
    "i cannot access",
)


class ChatOrchestrator:
    """
    Orchestrates the chat flow, handling intent classification,
    tool selection, and LLM interaction.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        registry: ToolRegistry,
        conversation_repo: Any,  # Avoid circular import type hint issues or use TYPE_CHECKING
        memory_repo: Any,
    ):
        self.provider = provider
        self.registry = registry
        self.conversation_repo = conversation_repo
        self.memory_repo = memory_repo
        self.settings = get_settings()
        # Always use Ollama for embeddings (Hybrid Architecture)
        self.embedding_service = EmbeddingService(base_url=self.settings.ollama_base_url)
        self.agentic_engine = AgenticEngine(provider=provider, registry=registry)
        # Fix 1.1: Cancellation signal for stream abort safety
        self._cancelled = asyncio.Event()

    def cancel(self):
        """Signal cancellation — called when client disconnects mid-stream."""
        self._cancelled.set()

    def _is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    # Fix 1.2: Token-aware context windowing
    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token for English."""
        if not text:
            return 0
        return len(text) // 4

    def _serialize_tool_result(self, result: Any) -> str:
        """Serialize tool output to a deterministic string for chat feedback."""
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            return str(result)

    def _prepare_tool_result(self, tool_name: str, result: Any) -> tuple[str, Dict[str, Any]]:
        """Cap oversized tool output before adding it to model context/persistence."""
        text = self._serialize_tool_result(result)
        original_chars = len(text)
        metadata: Dict[str, Any] = {
            "tool_name": tool_name,
            "result_chars": original_chars,
            "truncated": False,
        }

        if original_chars <= MAX_TOOL_RESULT_CHARS:
            return text, metadata

        head_chars = MAX_TOOL_RESULT_CHARS // 2
        tail_chars = MAX_TOOL_RESULT_CHARS - head_chars
        removed_chars = original_chars - (head_chars + tail_chars)
        truncated_text = (
            f"{text[:head_chars]}\n\n"
            f"[TRUNCATED {removed_chars} chars from tool output '{tool_name}']\n\n"
            f"{text[-tail_chars:]}"
        )
        metadata.update(
            {
                "truncated": True,
                "max_chars": MAX_TOOL_RESULT_CHARS,
                "removed_chars": removed_chars,
            }
        )
        logger.warning(
            "Truncated tool output for %s from %s to %s chars",
            tool_name,
            original_chars,
            len(truncated_text),
        )
        return truncated_text, metadata

    def _is_simple_directory_listing_request(self, user_input: str) -> bool:
        """Detect simple 'list files here' requests where we should enforce lightweight args."""
        text = user_input.strip().lower()
        if not text:
            return False
        intent_markers = ("list", "show", "display")
        target_markers = ("file", "files", "folder", "folders", "directory", "directories")
        location_markers = ("current", "here", "this directory", "working directory", "cwd")
        return (
            any(m in text for m in intent_markers)
            and any(m in text for m in target_markers)
            and any(m in text for m in location_markers)
        )

    def _restrict_tools_for_simple_listing(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep only directory listing tool to avoid unnecessary tool-expansion latency."""
        directory_tools = [t for t in tools if t.get("function", {}).get("name") == "directory_tree"]
        return directory_tools if directory_tools else tools

    def _optimize_directory_tree_args(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Constrain directory_tree calls for speed and bounded output size."""
        optimized = dict(tool_args or {})
        optimized["path"] = optimized.get("path") or "."

        existing = optimized.get("excludePatterns")
        excludes = existing if isinstance(existing, list) else []
        for pattern in SIMPLE_LISTING_EXCLUDE_PATTERNS:
            if pattern not in excludes:
                excludes.append(pattern)
        optimized["excludePatterns"] = excludes

        # Apply depth cap only if the schema already exposes a depth-like field.
        for depth_key in ("maxDepth", "depth", "max_depth"):
            if depth_key in optimized:
                try:
                    optimized[depth_key] = min(int(optimized[depth_key]), 2)
                except Exception:
                    optimized[depth_key] = 2
        return optimized

    def _infer_intent_override(self, user_input: str, available_categories: set[str]) -> Optional[str]:
        """Heuristic override when classifier under-routes obvious tool intents."""
        text = user_input.strip().lower()
        if not text:
            return None

        rules: List[tuple[str, tuple[str, ...]]] = [
            ("SQLITE", ("sqlite", "sql ", "table ", "insert ", "select ", "update ", "delete ")),
            ("MEMORY", ("remember", "recall", "saved", "my release region", "my favorite")),
            ("FILESYSTEM", ("file", "files", "directory", "folder", "readme", "path", "write")),
            ("GIT", ("git ", "branch", "commit", "diff", "status", "rebase")),
            ("FETCH", ("http://", "https://", "fetch ", "web ", "url", "website")),
            ("TIME", ("current utc", "current time", "what time", "timezone", "utc", "timestamp")),
        ]
        for category, markers in rules:
            if category in available_categories and any(m in text for m in markers):
                return category
        return None

    def _requires_tool_execution(self, intent: str, user_input: str, tools: List[Dict[str, Any]]) -> bool:
        """Determine when we should fail-closed if no tool execution occurs."""
        if intent == "GENERAL":
            return False

        text = user_input.strip().lower()
        if not text:
            return False

        conceptual_prefixes = (
            "what is",
            "explain",
            "difference between",
            "how does",
            "how do",
        )
        runtime_markers = (
            "current",
            "now",
            "list",
            "show",
            "read",
            "write",
            "create",
            "delete",
            "fetch",
            "status",
            "remember",
            "recall",
            "table",
            "query",
            "insert",
            "update",
            "select",
            "http://",
            "https://",
        )

        if text.startswith(conceptual_prefixes) and not any(m in text for m in runtime_markers):
            return False

        # If intent is tool-backed and request asks for runtime state/actions, enforce fail-closed.
        if any(m in text for m in runtime_markers):
            return True

        # Conservative default for non-GENERAL intents: require tools when available.
        return bool(tools)

    def _response_contains_simulation(self, content: str) -> bool:
        text = (content or "").strip().lower()
        return any(marker in text for marker in SIMULATION_MARKERS)

    def _build_fail_closed_response(
        self,
        intent: str,
        reason: str,
        tools: List[Dict[str, Any]],
        tool_errors: Optional[List[str]] = None,
    ) -> str:
        available = ", ".join(t["function"]["name"] for t in tools[:6]) if tools else "none"
        details = ""
        if tool_errors:
            details = f" Tool errors: {', '.join(tool_errors[:3])}."
        return (
            f"I can't verify this request reliably via tools yet, so I won't simulate a result. "
            f"Intent={intent}. Reason: {reason}. Available tools: {available}.{details}"
        )

    async def _build_context_window(
        self, messages: List[ChatMessage], conv_uuid, max_context_tokens: int = 24000
    ) -> List[ChatMessage]:
        """Assemble context window respecting token budget instead of flat message count."""
        # Reserve tokens: system prompt (~500), response (~2000)
        available = max_context_tokens - 2500

        result = []

        # Always keep system message
        if messages and messages[0].role == MessageRole.SYSTEM:
            system_tokens = self._estimate_tokens(messages[0].content)
            available -= system_tokens
            result.append(messages[0])
            messages = messages[1:]

        # Walk backwards from most recent, filling budget
        kept = []
        for msg in reversed(messages):
            msg_tokens = self._estimate_tokens(msg.content)
            if available - msg_tokens < 0:
                break
            kept.append(msg)
            available -= msg_tokens

        kept.reverse()

        # If we dropped messages, inject summary as bridge
        if len(kept) < len(messages):
            try:
                summary_data = await self.conversation_repo.get_conversation_summary(conv_uuid)
                if summary_data and summary_data.get("summary"):
                    bridge = ChatMessage(
                        role=MessageRole.SYSTEM,
                        content=f"[Earlier context summary]: {summary_data['summary']}"
                    )
                    result.append(bridge)
            except Exception as e:
                logger.warning(f"Could not inject summary bridge: {e}")

        result.extend(kept)
        return result

    async def run(
        self,
        conversation_id: str,
        user_input: str,
        conversation_history: List[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        user_id: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Main entry point for the orchestrator (Phase 3).
        Supports multimodal input (Phase 5.0).
        """
        import time

        start_time = time.time()

        conv_uuid = uuid.UUID(conversation_id)
        semantic_context = ""

        # --- Phase 5.0: Multimodal Detection ---
        has_images = False
        has_audio_transcription = False
        last_user_msg = conversation_history[-1] if conversation_history else None

        if last_user_msg and last_user_msg.attachments:
            for att in last_user_msg.attachments:
                if att.type == "image" and att.base64_data:
                    has_images = True
                if att.type in ("audio", "video") and att.transcription:
                    has_audio_transcription = True
                    # Inject transcription into the message content
                    if att.transcription not in (last_user_msg.content or ""):
                        prefix = (
                            "[Audio transcription]"
                            if att.type == "audio"
                            else "[Video audio transcription]"
                        )
                        last_user_msg.content = (
                            f"{last_user_msg.content}\n\n{prefix}: {att.transcription}"
                        ).strip()

        # Auto-switch to vision model when images are present
        if has_images:
            settings = get_settings()
            original_model = model
            model = settings.vision_model
            logger.info(
                f"Phase 5.0: Detected image attachments — switching model "
                f"from {original_model} to {model}"
            )

        # Fetch Long-Term Memory (Phase 2 Addition)
        if user_id:
            try:
                memories = await self.memory_repo.get_user_memories(uuid.UUID(user_id))
                user_context = "\nUser Profile:\n" + "\n".join([f"- {m.content}" for m in memories])
            except Exception as e:
                logger.error(f"Failed to fetch memories: {e}")
                user_context = ""
        else:
            user_context = ""

        # Fetch Conversation Summary (Phase 2.7)
        try:
            conv_summary_data = await self.conversation_repo.get_conversation_summary(conv_uuid)
            conv_summary = conv_summary_data["summary"] if conv_summary_data else None
            last_summarized_seq = (
                conv_summary_data["last_summarized_seq_id"] if conv_summary_data else 0
            )
        except Exception as e:
            logger.error(f"Failed to fetch conversation summary: {e}")
            conv_summary = None
            last_summarized_seq = 0

        # --- Phase 3.5: Context Cache Check ---
        context_cache_key = f"conversation:{conversation_id}:context"
        cached_context = await redis_client.get(context_cache_key)

        if cached_context:
            logger.info(f"Using cached context for conversation {conversation_id}")
            # cached_context is a dict containing user_context, semantic_context, and conv_summary
            if not user_context:
                user_context = cached_context.get("user_context", "")
            if not semantic_context:
                semantic_context = cached_context.get("semantic_context", "")
            if not conv_summary:
                conv_summary = cached_context.get("conv_summary", "")
        else:
            # We'll cache it after we've computed all parts
            pass

        # --- Fix 2.3: Fast-path bypass for trivial queries ---
        if TRIVIAL_PATTERNS.match(user_input.strip()):
            intent, complexity = "GENERAL", "SIMPLE"
            tools = []
            logger.info(f"Fast-path: trivial query bypass for '{user_input[:50]}'")
        else:
            # --- Phase 4+5.5: Intent + Complexity Classification ---
            intent, complexity = await self.agentic_engine.classify_intent_and_complexity(
                user_input, model, has_media=(has_images or has_audio_transcription)
            )
            available_categories = set(self.registry.get_categories())
            intent_override = self._infer_intent_override(user_input, available_categories)
            if intent_override and intent != intent_override:
                logger.info(
                    "Phase 4 guard: overriding intent %s -> %s based on query heuristics",
                    intent,
                    intent_override,
                )
                intent = intent_override
            logger.info(f"Phase 4: intent='{intent}', complexity='{complexity}'")

            # --- Phase 5: Tool Scope Reduction ---
            if complexity == "COMPLEX":
                tools = await self.agentic_engine.get_expanded_tools(intent, user_input)
            else:
                tools = await self._filter_tools(intent, user_input)
            logger.info(
                f"Phase 5: Selected {len(tools)} tools: {[t['function']['name'] for t in tools]}"
            )
        simple_listing_mode = (
            complexity == "SIMPLE"
            and intent == "FILESYSTEM"
            and self._is_simple_directory_listing_request(user_input)
        )
        if simple_listing_mode and tools:
            tools = self._restrict_tools_for_simple_listing(tools)
            logger.info(
                "Phase 5 opt: simple directory listing mode enabled; restricted tools to %s",
                [t["function"]["name"] for t in tools],
            )
        requires_tool_execution = self._requires_tool_execution(intent, user_input, tools)
        if requires_tool_execution:
            logger.info("Phase 5 guard: tool execution required for this request.")
        INTENT_CLASSIFICATION_TOTAL.labels(intent=intent).inc()

        # --- Phase 5.5: Semantic Memory Retrieval ---
        if not semantic_context:
            try:
                query_embedding = await self.embedding_service.generate_embedding(user_input)
                if query_embedding and user_id:
                    similar_msgs = await self.conversation_repo.search_similar_messages(
                        uuid.UUID(user_id), query_embedding, limit=3
                    )
                    if similar_msgs:
                        semantic_context = "\nRelevant Past Conversation Context:\n"
                        for m in similar_msgs:
                            semantic_context += f"- {m.role}: {m.content}\n"
                        logger.info(f"Phase 5.5: Retrieved {len(similar_msgs)} similar messages.")
            except Exception as e:
                logger.error(f"Semantic memory retrieval failed: {e}. Rolling back session to recover.")
                # If the SQL fails (e.g. pgvector missing), we MUST rollback to continue using the session
                try:
                    await self.conversation_repo.session.rollback()
                except Exception as rb_err:
                    logger.error(f"Second-level rollback failed: {rb_err}")

        # Update Context Cache
        await redis_client.set(
            context_cache_key,
            {
                "user_context": user_context,
                "semantic_context": semantic_context,
                "conv_summary": conv_summary,
            },
            ttl=3600,
        )

        # Prepare messages
        # Fix 1.2: Token-aware context windowing
        messages = await self._build_context_window(list(conversation_history), conv_uuid)
        current_seq = len(conversation_history)

        # Inject Dynamic System Prompt
        system_prompt = self._get_system_prompt(intent, bool(tools))
        if user_context:
            system_prompt += user_context
        if semantic_context:
            system_prompt += semantic_context
        if conv_summary:
            system_prompt += f"\n\nPrevious Conversation Summary:\n{conv_summary}\n"

        if messages and messages[0].role == MessageRole.SYSTEM:
            messages[0] = ChatMessage(role=MessageRole.SYSTEM, content=system_prompt)
        else:
            messages.insert(0, ChatMessage(role=MessageRole.SYSTEM, content=system_prompt))

        if requires_tool_execution and not tools:
            fail_content = self._build_fail_closed_response(
                intent=intent,
                reason="No matching tools were selected for a tool-dependent request",
                tools=tools,
            )
            current_seq += 1
            msg = await self.conversation_repo.add_message(
                conversation_id=conv_uuid,
                role=MessageRole.ASSISTANT,
                content=fail_content,
                sequence_number=current_seq,
                metadata={"model": model, "type": "fail_closed"},
                model=model,
            )
            asyncio.create_task(self._safe_embed(msg.id, fail_content))
            yield StreamChunk(content=fail_content, done=True)
            ORCHESTRATOR_REQUEST_DURATION_SECONDS.labels(intent=intent).observe(
                time.time() - start_time
            )
            return

        # --- Phase 5.5: Route COMPLEX to Agentic Engine ---
        if complexity == "COMPLEX" and tools:
            logger.info("Phase 5.5: Routing to agentic Plan+ReAct engine")
            self.last_usage = None  # Initialize usage tracking

            # Build conversation context for planner
            conv_context = ""
            if conv_summary:
                conv_context = f"Previous context: {conv_summary}"

            # Create plan
            tool_names = [t["function"]["name"] for t in tools]
            plan = await self.agentic_engine.create_plan(
                user_input, model, tool_names, conv_context
            )

            # Execute plan with ReAct loop
            agentic_content = ""
            agentic_tool_calls = []

            async for chunk in self.agentic_engine.execute(
                messages=messages,
                model=model,
                tools=tools,
                plan=plan,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                # Fix 1.1: Check for cancellation each iteration
                if self._is_cancelled():
                    logger.info("Orchestrator cancelled during agentic execution")
                    return
                agentic_content += chunk.content
                if chunk.usage:
                    self.last_usage = chunk.usage
                yield chunk

            # Persist final agentic response
            current_seq += 1
            msg = await self.conversation_repo.add_message(
                conversation_id=conv_uuid,
                role=MessageRole.ASSISTANT,
                content=agentic_content,
                sequence_number=current_seq,
                metadata={"model": model, "type": "agentic", "plan": plan},
                token_count_prompt=self.last_usage.prompt_tokens if self.last_usage else None,
                token_count_completion=self.last_usage.completion_tokens
                if self.last_usage
                else None,
                model=model,
            )
            # Fix 3.2: Fire-and-forget embeddings
            asyncio.create_task(self._safe_embed(msg.id, agentic_content))
            asyncio.create_task(self._safe_embed_user(conv_uuid, current_seq - 1))

            # Summarization check
            # Fix 3.2: Fire-and-forget summarization
            if (current_seq - last_summarized_seq) >= 20:
                asyncio.create_task(self._safe_summarize(
                    conv_uuid, current_seq, last_summarized_seq, model
                ))

            ORCHESTRATOR_REQUEST_DURATION_SECONDS.labels(intent=intent).observe(
                time.time() - start_time
            )
            return

        # --- Phase 6: Fast Path (SIMPLE) — One-shot flow ---
        current_tool_calls: List[ToolCall] = []
        full_content = ""
        self.last_usage = None  # Track usage from stream
        phase6_max_tokens = (
            min(max_tokens, SIMPLE_LISTING_PHASE6_MAX_TOKENS) if simple_listing_mode else max_tokens
        )

        # Streaming loop
        async for chunk in self.provider.stream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=phase6_max_tokens,
            tools=tools if tools else None,
        ):
            # Fix 1.1: Check for cancellation
            if self._is_cancelled():
                logger.info("Orchestrator cancelled during streaming")
                return

            if not (requires_tool_execution and tools):
                full_content += chunk.content
            if chunk.tool_calls:
                logger.info(
                    f"Phase 6: Detected {len(chunk.tool_calls)} tool calls from stream: {[tc.function.name for tc in chunk.tool_calls]}"
                )
                current_tool_calls.extend(chunk.tool_calls)
                # Always emit tool call traces so REST clients can inspect execution path.
                yield StreamChunk(content="", tool_calls=chunk.tool_calls, done=False)

            if not current_tool_calls and not (requires_tool_execution and tools):
                yield chunk
            else:
                pass

            # Capture usage from the last chunk if present
            if chunk.usage:
                self.last_usage = chunk.usage

        # Check for fallback parsing (Phase 6b)
        if not current_tool_calls and tools:
            parsed = self.provider._try_parse_tool_calls(full_content)
            if parsed:
                logger.info(f"Phase 6b: Parsed {len(parsed)} tool calls from content fallback")
                current_tool_calls = parsed
                yield StreamChunk(content="", tool_calls=parsed, done=False)

        # Guard: tool-dependent requests must produce an executable tool call (retry once with stricter prompt).
        if requires_tool_execution and tools and not current_tool_calls:
            logger.warning("Phase 6d: Required tool call missing — forcing one strict retry")
            try:
                forced_messages = list(messages) + [
                    ChatMessage(
                        role=MessageRole.SYSTEM,
                        content=(
                            "You must call exactly one available tool now. "
                            "Return a tool call only. Do not provide natural language."
                        ),
                    )
                ]
                forced = await self.provider.complete(
                    messages=forced_messages,
                    model=model,
                    temperature=0.0,
                    max_tokens=min(max_tokens, FORCED_TOOL_CALL_MAX_TOKENS),
                    tools=tools,
                )
                forced_tool_calls = forced.message.tool_calls
                if not forced_tool_calls and forced.message.content:
                    forced_tool_calls = self.provider._try_parse_tool_calls(forced.message.content)
                if forced_tool_calls:
                    current_tool_calls = forced_tool_calls
                    if forced.usage:
                        self.last_usage = forced.usage
                    yield StreamChunk(content="", tool_calls=forced_tool_calls, done=False)
                else:
                    full_content = self._build_fail_closed_response(
                        intent=intent,
                        reason="Model did not return a valid tool call",
                        tools=tools,
                    )
                    yield StreamChunk(content=full_content, done=True)
            except Exception as e:
                logger.error("Forced tool-call retry failed: %s", e)
                full_content = self._build_fail_closed_response(
                    intent=intent,
                    reason=f"Forced tool-call retry failed ({e})",
                    tools=tools,
                )
                yield StreamChunk(content=full_content, done=True)

        # Safety fallback: if LLM returned empty content AND no tool calls were captured,
        # retry without tools to get a natural language response
        if not full_content.strip() and not current_tool_calls and tools and not requires_tool_execution:
            logger.warning("Phase 6c: Empty response with tools — retrying without tools")
            full_content = ""
            self.last_usage = None
            async for chunk in self.provider.stream(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=phase6_max_tokens,
                tools=None,
            ):
                full_content += chunk.content
                if chunk.usage:
                    self.last_usage = chunk.usage
                yield chunk

        # --- Phase 7: Tool Execution ---
        if current_tool_calls:
            hitl_tools = set(get_hitl_tool_names())
            if any(tc.function.name in hitl_tools for tc in current_tool_calls):
                assistant_msg = ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=full_content,
                    tool_calls=current_tool_calls,
                )
                messages.append(assistant_msg)

                current_seq += 1
                await self.conversation_repo.add_message(
                    conversation_id=conv_uuid,
                    role=MessageRole.ASSISTANT,
                    content=full_content,
                    sequence_number=current_seq,
                    tool_calls=[t.model_dump() for t in current_tool_calls],
                    metadata={"model": model, "requires_confirmation": True},
                    token_count_prompt=self.last_usage.prompt_tokens if self.last_usage else None,
                    token_count_completion=self.last_usage.completion_tokens
                    if self.last_usage
                    else None,
                    model=model,
                )

                yield StreamChunk(
                    content="",
                    status="Awaiting your confirmation...",
                    tool_calls=current_tool_calls,
                    done=False,
                )
                return

            # Append assistant message with tool calls
            assistant_msg = ChatMessage(
                role=MessageRole.ASSISTANT, content=full_content, tool_calls=current_tool_calls
            )
            messages.append(assistant_msg)

            # Persist to DB
            current_seq += 1
            msg = await self.conversation_repo.add_message(
                conversation_id=conv_uuid,
                role=MessageRole.ASSISTANT,
                content=full_content,
                sequence_number=current_seq,
                tool_calls=[t.model_dump() for t in current_tool_calls],
                metadata={"model": model},
                token_count_prompt=self.last_usage.prompt_tokens if self.last_usage else None,
                token_count_completion=self.last_usage.completion_tokens
                if self.last_usage
                else None,
                model=model,
            )

            # Fix 3.2: Fire-and-forget embeddings (was blocking hot path)
            asyncio.create_task(self._safe_embed(msg.id, full_content))
            asyncio.create_task(self._safe_embed_user(conv_uuid, current_seq - 1))

            # Execute tools
            successful_tool_names: List[str] = []
            tool_errors: List[str] = []
            for tool_call in current_tool_calls:
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments
                original_tool_args = dict(tool_args or {})
                if simple_listing_mode and tool_name == "directory_tree":
                    tool_args = self._optimize_directory_tree_args(tool_args)

                yield StreamChunk(content="", status=f"Executing {tool_name}...", done=False)

                tool_metadata: Dict[str, Any] = {"tool_name": tool_name, "truncated": False}
                try:
                    tool_start = time.time()
                    tool = self.registry.get_tool(tool_name)
                    try:
                        raw_result = await tool.run(**tool_args)
                    except Exception:
                        # If constrained args are incompatible, retry once with original model args.
                        if simple_listing_mode and tool_name == "directory_tree" and tool_args != original_tool_args:
                            logger.warning(
                                "directory_tree optimized args failed; retrying with original args"
                            )
                            raw_result = await tool.run(**original_tool_args)
                        else:
                            raise
                    result, tool_metadata = self._prepare_tool_result(tool_name, raw_result)
                    successful_tool_names.append(tool_name)
                    TOOL_EXECUTION_TOTAL.labels(tool_name=tool_name, status="success").inc()
                except Exception as e:
                    logger.error(f"Tool execution failed: {e}")
                    TOOL_EXECUTION_TOTAL.labels(tool_name=tool_name, status="error").inc()
                    tool_errors.append(f"{tool_name}: {e}")
                    result = f"Error executing tool {tool_name}: {e}"
                finally:
                    TOOL_EXECUTION_DURATION_SECONDS.labels(tool_name=tool_name).observe(
                        time.time() - tool_start
                    )

                # Persist result
                current_seq += 1
                tool_msg = ChatMessage(
                    role=MessageRole.TOOL, content=str(result), tool_call_id=tool_call.id
                )
                messages.append(tool_msg)

                await self.conversation_repo.add_message(
                    conversation_id=conv_uuid,
                    role=MessageRole.TOOL,
                    content=str(result),
                    sequence_number=current_seq,
                    tool_call_id=tool_call.id,
                    metadata=tool_metadata,
                )

            # --- Phase 8: Tool Result Feedback Loop ---
            synthesis_content = ""
            self.last_usage = None  # Reset for synthesis
            synthesis_max_tokens = (
                min(max_tokens, SIMPLE_LISTING_SYNTHESIS_MAX_TOKENS)
                if simple_listing_mode
                else max_tokens
            )
            if requires_tool_execution and not successful_tool_names:
                synthesis_content = self._build_fail_closed_response(
                    intent=intent,
                    reason="All tool calls failed; refusing to fabricate results",
                    tools=tools,
                    tool_errors=tool_errors,
                )
                yield StreamChunk(content=synthesis_content, done=True)
            else:
                async for chunk in self.provider.stream(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=synthesis_max_tokens,
                    tools=None,
                ):
                    synthesis_content += chunk.content
                    if chunk.usage:
                        self.last_usage = chunk.usage
                    # For tool-critical flows, emit only validated final text to prevent
                    # leaking intermediate speculative synthesis chunks.
                    if not requires_tool_execution:
                        yield chunk

                if requires_tool_execution and self._response_contains_simulation(synthesis_content):
                    synthesis_content = self._build_fail_closed_response(
                        intent=intent,
                        reason="Model response contained simulated/unverified language",
                        tools=tools,
                        tool_errors=tool_errors,
                    )
                if requires_tool_execution:
                    yield StreamChunk(content=synthesis_content, done=True, usage=self.last_usage)

            # Persist synthesis
            current_seq += 1
            msg = await self.conversation_repo.add_message(
                conversation_id=conv_uuid,
                role=MessageRole.ASSISTANT,
                content=synthesis_content,
                sequence_number=current_seq,
                metadata={"model": model, "type": "synthesis"},
                token_count_prompt=self.last_usage.prompt_tokens if self.last_usage else None,
                token_count_completion=self.last_usage.completion_tokens
                if self.last_usage
                else None,
                model=model,
            )
            asyncio.create_task(self._safe_embed(msg.id, synthesis_content))

        else:
            # Persist final response
            current_seq += 1
            msg = await self.conversation_repo.add_message(
                conversation_id=conv_uuid,
                role=MessageRole.ASSISTANT,
                content=full_content,
                sequence_number=current_seq,
                metadata={"model": model},
                token_count_prompt=self.last_usage.prompt_tokens if self.last_usage else None,
                token_count_completion=self.last_usage.completion_tokens
                if self.last_usage
                else None,
                model=model,
            )
            # Fix 3.2: Fire-and-forget + removed duplicate embed call
            asyncio.create_task(self._safe_embed(msg.id, full_content))
            asyncio.create_task(self._safe_embed_user(conv_uuid, current_seq - 1))

        # --- Phase 9: Background Summarization (Fix 3.2: fire-and-forget) ---
        if (current_seq - last_summarized_seq) >= 20:
            asyncio.create_task(self._safe_summarize(
                conv_uuid, current_seq, last_summarized_seq, model
            ))

        # Record total duration
        ORCHESTRATOR_REQUEST_DURATION_SECONDS.labels(intent=intent).observe(
            time.time() - start_time
        )

    async def _summarize_conversation(
        self, conversation_id: Any, current_seq: int, last_seq: int, model: str
    ):
        """
        Summarize the conversation from last_seq to current_seq.
        """
        try:
            # Fetch unsummarized messages
            # We need a repo method to fetch range.
            # Or we just fetch recent (limit=current-last)
            limit = current_seq - last_seq
            # Limit might be large if we haven't summarized in a while.
            # Let's cap it at 100 to avoid context blowup during summarization
            fetch_limit = min(limit, 100)

            recent_msgs = await self.conversation_repo.get_recent_messages(
                conversation_id, limit=fetch_limit
            )
            # recent_msgs are reversed (newest first). Re-reverse to chronological
            messages_to_summarize = list(reversed(recent_msgs))

            text_to_summarize = "\n".join([f"{m.role}: {m.content}" for m in messages_to_summarize])

            summary_prompt = (
                "Summarize the following conversation segment efficiently. "
                "Focus on key facts, user preferences, and important decisions. "
                "Do not lose important details.\n\n"
                f"{text_to_summarize}"
            )

            # Call LLM for summary
            response = await self.provider.complete(
                messages=[ChatMessage(role=MessageRole.USER, content=summary_prompt)],
                model=model,
                max_tokens=200,
                temperature=0.3,
            )

            new_segment_summary = response.message.content

            # Update DB
            # If existing summary exists, append/merge?
            # For MVP: "Previous Summary + New Segment" -> Updated Summary
            # But that grows indefinitely.
            # Better: "Update the summary with new info".

            current_summary_data = await self.conversation_repo.get_conversation_summary(
                conversation_id
            )
            old_summary = current_summary_data["summary"] if current_summary_data else ""

            if old_summary:
                update_prompt = (
                    "Here is the previous conversation summary:\n"
                    f"{old_summary}\n\n"
                    "Here is the new conversation segment:\n"
                    f"{new_segment_summary}\n\n"
                    "Create a consolidated summary of the entire conversation. Keep it concise."
                )
                response = await self.provider.complete(
                    messages=[ChatMessage(role=MessageRole.USER, content=update_prompt)],
                    model=model,
                    max_tokens=300,
                    temperature=0.3,
                )
                final_summary = response.message.content
            else:
                final_summary = new_segment_summary

            await self.conversation_repo.update_summary(conversation_id, final_summary, current_seq)
            logger.info(f"Updated summary for conversation {conversation_id} at seq {current_seq}")

        except Exception as e:
            logger.error(f"Summarization failed: {e}")

    async def _embed_message(self, message_id: Any, content: str):
        """Generate and save embedding for a message."""
        from chatbot_ai_system.config import get_settings
        if get_settings().disable_background_embedding:
            return

        try:
            embedding = await self.embedding_service.generate_embedding(content)
            if embedding:
                await self.conversation_repo.update_message_embedding(message_id, embedding)
                logger.info(f"Generated embedding for message {message_id}")
        except Exception as e:
            logger.error(f"Failed to generate embedding for message {message_id}: {e}")

    async def _safe_embed(self, message_id: Any, content: str):
        """Fire-and-forget wrapper for _embed_message with error isolation."""
        try:
            await self._embed_message(message_id, content)
        except Exception as e:
            logger.error(f"Background embed failed for {message_id}: {e}")

    async def _safe_embed_user(self, conversation_id: UUID, sequence_number: int):
        """Fire-and-forget wrapper for _embed_user_message."""
        try:
            await self._embed_user_message(conversation_id, sequence_number)
        except Exception as e:
            logger.error(f"Background user embed failed at seq {sequence_number}: {e}")

    async def _safe_summarize(self, conversation_id: Any, current_seq: int, last_seq: int, model: str):
        """Fire-and-forget wrapper for _summarize_conversation."""
        try:
            await self._summarize_conversation(conversation_id, current_seq, last_seq, model)
        except Exception as e:
            logger.error(f"Background summarization failed: {e}")

    async def _embed_user_message(self, conversation_id: UUID, sequence_number: int):
        """Find the user message by sequence number and embed it."""
        try:
            # We need to find the message in DB
            from sqlalchemy import select

            from chatbot_ai_system.database.models import Message

            statement = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .where(Message.sequence_number == sequence_number)
                .where(Message.role == MessageRole.USER)
            )
            result = await self.conversation_repo.session.execute(statement)
            message = result.scalar_one_or_none()
            if message and inspect.isawaitable(message):
                # When tests mock the DB, it may return an unawaited coroutine.
                message = await message

            if message and not message.embedding:
                await self._embed_message(message.id, message.content)
        except Exception as e:
            logger.error(f"Failed to embed user message at seq {sequence_number}: {e}")

    async def _classify_intent(self, user_input: str, model: str, has_media: bool = False) -> str:
        """
        Phase 4: Classify user intent to determine tool needs.
        """
        # If media is present, skip LLM classifier — it's always VISION or GENERAL
        if has_media:
            return "GENERAL"

        # Simple zero-shot classifier
        classifier_messages = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=(
                    "You are an intent classifier. Analyze the user's request.\n"
                    "Categories:\n"
                    "1. GIT: Version control, commits, branches, diffs.\n"
                    "2. FILESYSTEM: Reading/writing files, listing directories, searching.\n"
                    "3. FETCH: Web requests, extracting content from URLs.\n"
                    "4. GENERAL: General knowledge, coding advice (without file access), greetings.\n"
                    "Output ONLY the category name (e.g., 'GIT')."
                ),
            ),
            ChatMessage(role=MessageRole.USER, content=user_input),
        ]

        response = await self.provider.complete(
            messages=classifier_messages, model=model, max_tokens=10, temperature=0.1
        )

        intent = response.message.content.strip().upper()
        # Fallback normalization
        if "GIT" in intent:
            return "GIT"
        if "FILE" in intent:
            return "FILESYSTEM"
        if "FETCH" in intent:
            return "FETCH"
        return "GENERAL"

    async def _filter_tools(self, intent: str, user_input: str) -> List[Dict[str, Any]]:
        """
        Phase 5: Reduce tool scope based on intent.
        Includes both MCP remote tools and local registered tools.
        """
        if intent == "GENERAL":
            return []
        intent = intent.upper()

        # Category-first (deterministic and most reliable for correctness).
        category_tools = self.registry.get_tools_by_category(intent)
        query_tools = await self.registry.get_ollama_tools(query=user_input)
        local_tools = [t.to_ollama_format() for t in self.registry._tools.values()]

        intent_keywords: Dict[str, List[str]] = {
            "FILESYSTEM": ["file", "dir", "directory", "folder", "read", "write", "list", "path"],
            "GIT": ["git", "repo", "branch", "commit", "diff", "status", "merge", "rebase"],
            "FETCH": ["fetch", "http", "url", "web", "search", "request", "download", "browse"],
            "TIME": ["time", "utc", "timezone", "timestamp"],
            "MEMORY": ["memory", "remember", "recall", "entity", "graph", "observation"],
            "SQLITE": ["sqlite", "sql", "query", "table", "insert", "update", "select", "delete"],
        }
        priority_names: Dict[str, List[str]] = {
            "TIME": ["current_time", "get_current_time", "convert_time", "get_timestamp"],
            "FETCH": ["fetch_html", "fetch_markdown", "fetch_txt", "fetch_json", "web_search_duckduckgo"],
            "MEMORY": ["create_entities", "add_observations", "search_nodes", "open_nodes", "read_graph"],
            "SQLITE": ["query", "execute", "create-table", "insert-record", "list-tables"],
        }

        filtered: List[Dict[str, Any]] = []
        seen_names: set[str] = set()

        def add_tool(tool: Dict[str, Any]) -> None:
            name = tool["function"]["name"]
            if name not in seen_names:
                filtered.append(tool)
                seen_names.add(name)

        for tool in category_tools:
            add_tool(tool)

        keywords = intent_keywords.get(intent, [])
        preferred = set(priority_names.get(intent, []))
        for tool in query_tools + local_tools:
            name = tool["function"]["name"]
            low = f"{name.lower()} {tool['function'].get('description', '').lower()}"
            if name in preferred or any(k in low for k in keywords):
                add_tool(tool)

        # Last-resort fallback: if category tools are empty, keep a small query-derived candidate set.
        if not filtered:
            for tool in query_tools[:6]:
                add_tool(tool)

        logger.info(
            "Phase 5: _filter_tools for intent=%s: %s selected (names=%s)",
            intent,
            len(filtered),
            [t["function"]["name"] for t in filtered],
        )
        return filtered[:12]

    def _get_system_prompt(self, intent: str, has_tools: bool) -> str:
        """
        Get the appropriate system prompt based on intent and tool availability.
        """
        base_prompt = "You are a helpful AI assistant."

        if not has_tools:
            return (
                base_prompt
                + "\nAnswer using your internal knowledge. Do not hallucinate or fabricate tool calls."
            )

        tool_instructions = (
            "\nYou have access to external tools via MCP.\n"
            "1. If the user's request requires it, call the appropriate tool.\n"
            "2. Output a valid JSON tool call.\n"
            "3. Use tool results as the source of truth.\n"
            "4. Never claim an action/result unless a tool output in this conversation proves it.\n"
            "5. Never simulate, assume, or fabricate execution."
        )

        return base_prompt + tool_instructions
