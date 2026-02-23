"""
Agentic Engine — Phase 5.5

Plan + ReAct hybrid for complex multi-step tasks.
- Planner: creates a numbered step list from the user's request
- Executor: runs each step using ReAct (tool call → observe → next step)
- Re-planner: adapts remaining steps when unexpected results appear
- Tool expansion: adds cross-category tools mid-loop if needed
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Tuple

from chatbot_ai_system.models.schemas import (
    ChatMessage,
    MessageRole,
    StreamChunk,
    ToolCall,
)

logger = logging.getLogger(__name__)

# --- Configuration ---
MAX_AGENT_ROUNDS = 8  # Safety cap on total tool rounds
AGENT_TIMEOUT_SEC = 300  # Hard timeout for entire agentic flow
MAX_TOOLS_AGENTIC = 8  # Tool count cap (slightly higher than one-shot's 5)
MAX_TOOL_RETRIES = 2  # Fix 2.1: Per-tool retry limit
PER_TOOL_TIMEOUT_SEC = 30  # Fix 2.1: Per-tool execution timeout
MAX_CONSECUTIVE_FAILURES = 2  # Fix 2.2: Circuit breaker threshold


class AgenticEngine:
    """
    Plan + ReAct executor for complex multi-step tasks.

    Flow:
    1. Classifier determines INTENT + COMPLEXITY (in orchestrator)
    2. If COMPLEX → AgenticEngine takes over
    3. Planner creates a step list
    4. Executor runs each step, calling tools as needed
    5. If unexpected results → re-plan remaining steps
    6. Streams status per step to user
    """

    def __init__(self, provider, registry):
        """
        Args:
            provider: BaseLLMProvider instance
            registry: ToolRegistry instance
        """
        self.provider = provider
        self.registry = registry

    # ------------------------------------------------------------------ #
    # Fix 2.1: Tool execution with retry and structured errors
    # ------------------------------------------------------------------ #

    async def _execute_tool_with_retry(
        self, tool_name: str, tool_args: dict
    ) -> tuple:
        """Execute tool with retry and per-tool timeout.
        
        Returns:
            (result_string, success_bool)
        """
        try:
            tool = self.registry.get_tool(tool_name)
        except ValueError:
            return (
                f"[TOOL_ERROR] Tool '{tool_name}' not found in registry. "
                f"DO NOT fabricate results. Acknowledge this tool is unavailable.",
                False,
            )

        for attempt in range(MAX_TOOL_RETRIES):
            try:
                result = await asyncio.wait_for(
                    tool.run(**tool_args),
                    timeout=PER_TOOL_TIMEOUT_SEC,
                )
                return (str(result), True)
            except asyncio.TimeoutError:
                logger.warning(
                    f"Tool {tool_name} timeout (attempt {attempt + 1}/{MAX_TOOL_RETRIES})"
                )
                if attempt < MAX_TOOL_RETRIES - 1:
                    await asyncio.sleep(1)  # Brief backoff before retry
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e}")
                break  # Non-timeout errors are not retried

        return (
            f"[TOOL_ERROR] {tool_name} failed after {MAX_TOOL_RETRIES} attempts. "
            f"DO NOT fabricate results from this tool. Acknowledge the failure "
            f"and answer using your internal knowledge, clearly stating what "
            f"you could not verify.",
            False,
        )

    # ------------------------------------------------------------------ #
    # Complexity Classifier
    # ------------------------------------------------------------------ #

    async def classify_intent_and_complexity(
        self, user_input: str, model: str, has_media: bool = False
    ) -> Tuple[str, str]:
        """
        Single LLM call to classify both intent and complexity.

        Returns:
            (intent, complexity) where:
            - intent: GIT | FILESYSTEM | FETCH | GENERAL
            - complexity: SIMPLE | COMPLEX
        """
        # Media always routes to GENERAL + SIMPLE (handled by vision model)
        if has_media:
            return ("GENERAL", "SIMPLE")

        # Dynamic Category Discovery
        all_categories = self.registry.get_categories()

        # Build prompt description
        cat_desc = []
        for cat in all_categories:
            if cat == "GIT":
                cat_desc.append("   GIT: Version control, commits, branches, diffs, blame.")
            elif cat == "FILESYSTEM":
                cat_desc.append(
                    "   FILESYSTEM: Reading/writing files, listing directories, searching files."
                )
            elif cat == "FETCH":
                cat_desc.append(
                    "   FETCH: Web search, web requests, URLs, researching topics online, finding information about something, downloading content from the internet."
                )
            elif cat == "GENERAL":
                cat_desc.append(
                    "   GENERAL: General knowledge, coding advice, greetings, math, explanations."
                )
            else:
                # Fallback for dynamic MCP categories
                cat_desc.append(f"   {cat}: Tools for {cat.lower()} operations.")

        intent_section = "\n".join(cat_desc)

        classifier_prompt = (
            "You are a query analyzer. Given the user's message, output TWO things.\n\n"
            "1. INTENT — which category of tools is needed:\n"
            f"{intent_section}\n\n"
            "2. COMPLEXITY — how many steps are needed:\n"
            "   SIMPLE: Can be answered in ONE step (single tool call or direct knowledge).\n"
            "     Examples: 'What is Python?', 'Read file.py', 'Show git status'\n"
            "   COMPLEX: Needs MULTIPLE steps where later steps depend on earlier results.\n"
            "     Examples: 'Read error log AND find the bug in source code',\n"
            "              'Compare files A and B and summarize differences',\n"
            "              'Search web for X then check our code for Y'\n\n"
            "   Signals of COMPLEX:\n"
            "     - Multiple files/resources/actions mentioned\n"
            "     - Sequential words: 'and then', 'after that', 'first...then'\n"
            "     - Analysis words: 'compare', 'analyze', 'debug', 'investigate', 'research'\n"
            "     - Conditional: 'if X then Y'\n\n"
            "Output EXACTLY two lines, nothing else:\n"
            "INTENT: <category>\n"
            "COMPLEXITY: <level>"
        )

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=classifier_prompt),
            ChatMessage(role=MessageRole.USER, content=user_input),
        ]

        response = await self.provider.complete(
            messages=messages,
            model=model,
            max_tokens=20,
            temperature=0.1,
        )

        text = response.message.content.strip().upper()

        # Valid intents for Phase 5.5 (Dynamic)
        valid_intents = set(all_categories)  # Use the same list we generated for prompt

        # Parse intent
        intent = "GENERAL"

        # Robust parsing: check each line against valid intents
        for line in text.splitlines():
            # Normalize: remove "INTENT:" prefix, strip, uppercase
            clean_line = line.replace("INTENT:", "").strip().upper()

            # 1. Exact match
            if clean_line in valid_intents:
                intent = clean_line
                break

            # 2. Substring match (e.g. "INTENT: GIT OPERATIONS")
            # Sort by length desc to match "FILESYSTEM" before "FILE"
            for valid in sorted(valid_intents, key=len, reverse=True):
                if valid in clean_line:
                    intent = valid
                    break

            if intent != "GENERAL":
                break

        # Parse complexity
        complexity = "SIMPLE"  # Default to fast path

        # Robust parsing: check for COMPLEX value specifically after the key
        # We iterate lines to find the line with COMPLEXITY
        for line in text.splitlines():
            if "COMPLEXITY" in line:
                # Get part after the key to avoid matching the key itself
                try:
                    val = line.upper().split("COMPLEXITY", 1)[1]
                    if "COMPLEX" in val:
                        complexity = "COMPLEX"
                except IndexError:
                    pass

        logger.info(
            f"Phase 5.5 classifier: intent={intent}, complexity={complexity} (raw: {text!r})"
        )
        return (intent, complexity)

    # ------------------------------------------------------------------ #
    # Planner
    # ------------------------------------------------------------------ #

    async def create_plan(
        self,
        user_input: str,
        model: str,
        tool_names: List[str],
        conversation_context: str = "",
    ) -> List[str]:
        """
        Create a numbered step-by-step plan for a complex task.

        Returns:
            List of step descriptions (strings).
        """
        tools_desc = ", ".join(tool_names) if tool_names else "none"
        context_note = ""
        if conversation_context:
            context_note = f"\n\nRelevant conversation context:\n{conversation_context}\n"

        planner_prompt = (
            "You are a task planner. Break the user's request into a step-by-step plan.\n\n"
            "Rules:\n"
            "1. Each step must be ONE concrete action.\n"
            "2. Steps should be in logical order — later steps can use results of earlier steps.\n"
            "3. Keep steps concise (one line each).\n"
            "4. 3-6 steps maximum. Do not over-plan.\n"
            "5. The final step should synthesize/summarize the results.\n"
            f"6. Available tools: {tools_desc}\n"
            f"{context_note}\n"
            "Output a numbered list ONLY, nothing else.\n"
            "Example:\n"
            "1. Read the error log file\n"
            "2. Identify the source file and line from the error\n"
            "3. Read the relevant source file\n"
            "4. Analyze the bug and suggest a fix"
        )

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=planner_prompt),
            ChatMessage(role=MessageRole.USER, content=user_input),
        ]

        response = await self.provider.complete(
            messages=messages,
            model=model,
            max_tokens=300,
            temperature=0.3,
        )

        # Parse numbered list
        steps = []
        for line in response.message.content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # Remove numbering: "1. Do X" → "Do X"
            if len(line) > 2 and line[0].isdigit() and line[1] in ".)":
                line = line[2:].strip()
            elif len(line) > 3 and line[:2].isdigit() and line[2] in ".)":
                line = line[3:].strip()
            if line:
                steps.append(line)

        # Safety: cap at 6 steps
        steps = steps[:6]

        if not steps:
            steps = ["Analyze the request and provide a comprehensive answer"]

        logger.info(f"Phase 5.5 planner: {len(steps)} steps — {steps}")
        return steps

    def _detect_language(self, text: str) -> str:
        """Simple language detection based on common scripts."""
        import re
        # Check for Thai characters
        if re.search(r'[\u0e00-\u0e7f]', text):
            return "THAI"
        # Check for Japanese characters
        if re.search(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]', text):
            return "JAPANESE"
        # Default to English
        return "ENGLISH"

    # ------------------------------------------------------------------ #
    # Tool Expansion
    # ------------------------------------------------------------------ #

    async def get_expanded_tools(self, intent: str, user_input: str) -> List[Dict[str, Any]]:
        """
        Get tools for agentic mode — dynamic and cross-category.
        """
        # 1. Tools for the main intent
        candidates = self.registry.get_tools_by_category(intent)

        # 2. Cross-category tools if mentioned in query
        q = user_input.lower()
        all_cats = self.registry.get_categories()

        for cat in all_cats:
            if cat == intent or cat == "GENERAL":
                continue
            # If category name appears in query, add its tools
            if cat.lower() in q:
                cat_tools = self.registry.get_tools_by_category(cat)
                candidates.extend(cat_tools)

        # 3. Always include GENERAL tools if they match keywords?
        # Or just rely on the fallback below?
        # For now, let's mix in GENERAL tools if keywords match
        general_tools = self.registry.get_tools_by_category("GENERAL")
        for tool in general_tools:
            name = tool["function"]["name"].lower()
            # Simple keyword match for local tools
            if any(token in q for token in name.split("_")):
                candidates.append(tool)

        # 4. Deduplicate and Cap
        unique_tools = []
        seen = set()
        for t in candidates:
            name = t["function"]["name"]
            if name not in seen:
                unique_tools.append(t)
                seen.add(name)

        return unique_tools[:MAX_TOOLS_AGENTIC]

    def _needs_tool_expansion(
        self, reasoning_text: str, current_tools: List[Dict[str, Any]]
    ) -> bool:
        """
        Check if LLM's reasoning suggests it needs tools from a category we missed.
        Dynamic check against all registered categories.
        """
        text = reasoning_text.lower()
        all_cats = self.registry.get_categories()

        # Get set of categories currently present
        current_cats = set()
        # This is tricky because tools don't carry their category in the schema dict explicitly
        # But we can infer it or just check if *any* tool from a cat is present?
        # Simpler: check if we should add a category that isn't represented.

        # But wait, checking if a category is "represented" requires knowing which tool maps to which cat.
        # We can do a reverse lookup or just simply:
        # If "fetch" is in text, and we expect "FETCH" tools (e.g. fetch_html), are they there?

        current_names = {t["function"]["name"] for t in current_tools}

        for cat in all_cats:
            if cat == "GENERAL":
                continue

            # Use category name as keyword (e.g. "git", "filesystem", "fetch")
            # This relies on the convention that MCP names are descriptive
            if cat.lower() in text:
                # Check if we have any tools from this category
                cat_tools = self.registry.get_tools_by_category(cat)
                if not cat_tools:
                    continue

                # If valid tools exist for this cat, check if we have at least one
                cat_tool_names = {t["function"]["name"] for t in cat_tools}
                if not any(name in current_names for name in cat_tool_names):
                    return True

        return False

    async def _expand_tools_midloop(
        self, reasoning_text: str, current_tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Add missing tools detected from LLM reasoning."""
        text = reasoning_text.lower()
        all_cats = self.registry.get_categories()
        current_names = {t["function"]["name"] for t in current_tools}
        expanded = list(current_tools)

        for cat in all_cats:
            if cat == "GENERAL":
                continue

            if cat.lower() in text:
                cat_tools = self.registry.get_tools_by_category(cat)
                for tool in cat_tools:
                    name = tool["function"]["name"]
                    if name not in current_names:
                        expanded.append(tool)
                        current_names.add(name)
                        if len(expanded) >= MAX_TOOLS_AGENTIC:
                            break
            if len(expanded) >= MAX_TOOLS_AGENTIC:
                break

        logger.info(f"Phase 5.5 tool expansion: {len(current_tools)} → {len(expanded)} tools")
        return expanded[:MAX_TOOLS_AGENTIC]

    # ------------------------------------------------------------------ #
    # Plan + ReAct Executor
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        messages: List[ChatMessage],
        model: str,
        tools: List[Dict[str, Any]],
        plan: List[str],
        temperature: float = 0.7,
        max_tokens: int = 1500,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Execute a plan using ReAct-style reasoning per step.

        Yields:
            StreamChunk objects (status updates + final response).
        """
        start_time = time.time()
        total_steps = len(plan)
        round_num = 0
        all_tool_calls = []  # Track for persistence

        # Fix 2.2: Cycle detection and circuit breaker state
        tool_call_history = set()  # Set of (name, args_hash) tuples
        consecutive_failures = 0

        # Stream the plan to user
        plan_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan))
        yield StreamChunk(
            content="",
            status=f"📋 Plan ({total_steps} steps):\n{plan_text}",
            done=False,
        )

        # Build agentic system prompt
        user_lang = self._detect_language(messages[-1].content if messages else "")
        agentic_prompt = self._get_agentic_system_prompt(plan, tools, user_lang)

        # Inject/replace system message
        if messages and messages[0].role == MessageRole.SYSTEM:
            original_system = messages[0].content
            messages[0] = ChatMessage(
                role=MessageRole.SYSTEM,
                content=original_system + "\n\n" + agentic_prompt,
            )
        else:
            messages.insert(0, ChatMessage(role=MessageRole.SYSTEM, content=agentic_prompt))

        # Add a guidance message to start execution
        guidance = ChatMessage(
            role=MessageRole.USER,
            content=(
                f"Execute the plan step by step. You are on step 1 of {total_steps}. "
                f"Step 1: {plan[0]}\n\n"
                "Call the appropriate tool for this step. When you have completed ALL steps "
                "and have enough information, provide your final comprehensive answer as text."
            ),
        )
        messages.append(guidance)

        # Fix 2.4: Build tool name whitelist
        available_tool_names = {t["function"]["name"] for t in tools}

        # --- ReAct Loop ---
        current_step = 0

        for round_num in range(MAX_AGENT_ROUNDS):
            # Timeout check
            elapsed = time.time() - start_time
            if elapsed > AGENT_TIMEOUT_SEC:
                logger.warning(f"Phase 5.5: Timeout after {elapsed:.1f}s, {round_num} rounds")
                yield StreamChunk(
                    content="",
                    status="⏱️ Timeout reached — generating best answer with available info...",
                    done=False,
                )
                break

            # Fix 2.2: Circuit breaker check
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    f"Phase 5.5: Circuit breaker tripped after {consecutive_failures} consecutive failures"
                )
                yield StreamChunk(
                    content="",
                    status="⚠️ Multiple tool failures — synthesizing with available info...",
                    done=False,
                )
                break

            # LLM call — may produce tool calls or final text
            full_content = ""
            current_tool_calls: List[ToolCall] = []
            last_usage = None

            async for chunk in self.provider.stream(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools if tools else None,
            ):
                full_content += chunk.content
                if chunk.tool_calls:
                    current_tool_calls.extend(chunk.tool_calls)
                if chunk.usage:
                    last_usage = chunk.usage

            # Fallback parsing for models that output raw JSON
            if not current_tool_calls and tools:
                parsed = self.provider._try_parse_tool_calls(full_content)
                if parsed:
                    current_tool_calls = parsed
                    full_content = ""  # Clear raw JSON

            # --- No tool calls = LLM produced final answer ---
            if not current_tool_calls:
                step_label = f"Step {current_step + 1}/{total_steps}"
                yield StreamChunk(
                    content="",
                    status=f"📋 {step_label}: Synthesizing final answer... ✅",
                    done=False,
                )
                # Stream final answer
                yield StreamChunk(content=full_content, done=True, usage=last_usage)
                logger.info(
                    f"Phase 5.5: Completed in {round_num + 1} rounds, "
                    f"{time.time() - start_time:.1f}s"
                )
                return

            # --- Has tool calls → execute them ---
            # Append assistant message
            assistant_msg = ChatMessage(
                role=MessageRole.ASSISTANT,
                content=full_content,
                tool_calls=current_tool_calls,
            )
            messages.append(assistant_msg)

            for tool_call in current_tool_calls:
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments

                # Fix 2.4: Validate tool name against whitelist
                if tool_name not in available_tool_names:
                    result = (
                        f"[INVALID_TOOL] '{tool_name}' is not available. "
                        f"Available tools: {', '.join(sorted(available_tool_names))}. "
                        f"Call one of these tools instead."
                    )
                    tool_msg = ChatMessage(
                        role=MessageRole.TOOL,
                        content=result,
                        tool_call_id=tool_call.id,
                    )
                    messages.append(tool_msg)
                    consecutive_failures += 1
                    continue

                # Fix 2.2: Cycle detection — has this exact call been made before?
                try:
                    args_hash = hash(str(sorted(tool_args.items())) if isinstance(tool_args, dict) else str(tool_args))
                except Exception:
                    args_hash = hash(str(tool_args))
                call_signature = (tool_name, args_hash)

                if call_signature in tool_call_history:
                    result = (
                        f"[CYCLE_DETECTED] You already called {tool_name} with identical "
                        f"arguments and got the same result. Do NOT repeat this call. "
                        f"Move to the next step or provide your final answer."
                    )
                    tool_msg = ChatMessage(
                        role=MessageRole.TOOL,
                        content=result,
                        tool_call_id=tool_call.id,
                    )
                    messages.append(tool_msg)
                    consecutive_failures += 1
                    continue

                tool_call_history.add(call_signature)

                # Status update
                step_label = f"Step {min(current_step + 1, total_steps)}/{total_steps}"
                yield StreamChunk(
                    content="",
                    status=f"📋 {step_label}: Calling {tool_name}...",
                    done=False,
                )

                # Fix 2.1: Execute with retry and structured errors
                result, success = await self._execute_tool_with_retry(tool_name, tool_args)
                if success:
                    consecutive_failures = 0
                    logger.info(
                        f"Phase 5.5 round {round_num + 1}: {tool_name} completed"
                    )
                else:
                    consecutive_failures += 1
                    logger.warning(
                        f"Phase 5.5 round {round_num + 1}: {tool_name} failed "
                        f"(consecutive: {consecutive_failures})"
                    )

                # Append tool result
                tool_msg = ChatMessage(
                    role=MessageRole.TOOL,
                    content=result,
                    tool_call_id=tool_call.id,
                )
                messages.append(tool_msg)
                all_tool_calls.append(tool_call)

                # Status: step done
                status_icon = "✅" if success else "❌"
                yield StreamChunk(
                    content="",
                    status=f"📋 {step_label}: {tool_name} {status_icon}",
                    done=False,
                )

            # Advance step counter
            current_step += 1

            # Mid-loop tool expansion check
            if self._needs_tool_expansion(full_content, tools):
                tools = await self._expand_tools_midloop(full_content, tools)

            # Guide LLM to next step
            if current_step < total_steps:
                next_guidance = ChatMessage(
                    role=MessageRole.USER,
                    content=(
                        f"Good. Now proceed to step {current_step + 1} of {total_steps}: "
                        f"{plan[current_step]}\n\n"
                        "Call the appropriate tool, or if you have enough information "
                        "to answer directly, provide your final comprehensive answer."
                    ),
                )
                messages.append(next_guidance)
            else:
                # All steps done — ask for synthesis
                synthesis_guidance = ChatMessage(
                    role=MessageRole.USER,
                    content=(
                        "All planned steps are complete. Now synthesize ALL the information "
                        "you gathered and provide a comprehensive final answer to the "
                        "original request. Do NOT call any more tools."
                    ),
                )
                messages.append(synthesis_guidance)

        # --- Max rounds reached or timeout → force final synthesis ---
        yield StreamChunk(
            content="",
            status="🧠 Generating final answer...",
            done=False,
        )

        synthesis_content = ""
        last_usage = None
        async for chunk in self.provider.stream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=None,  # No tools — force text output
        ):
            synthesis_content += chunk.content
            if chunk.usage:
                last_usage = chunk.usage
            # YIELD EACH CHUNK IMMEDIATELY (Fix for response silence)
            yield chunk

        # Send final done chunk if not already sent by provider
        if not synthesis_content.strip():
             yield StreamChunk(content="No further information found.", done=True, usage=last_usage)

        logger.info(
            f"Phase 5.5: Force-synthesized after {round_num + 1} rounds, "
            f"{time.time() - start_time:.1f}s"
        )

    # ------------------------------------------------------------------ #
    # Agentic System Prompt
    # ------------------------------------------------------------------ #

    def _get_agentic_system_prompt(self, plan: List[str], tools: List[Dict[str, Any]], language: str = "ENGLISH") -> str:
        """Build the agentic system prompt with plan and tool discipline."""
        tool_names = [t["function"]["name"] for t in tools]
        tools_list = ", ".join(tool_names) if tool_names else "none"
        plan_text = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(plan))
        
        lang_instruction = "You MUST respond in English."
        if language == "THAI":
             lang_instruction = "You MUST respond in Thai (ภาษาไทย)."
        elif language == "JAPANESE":
             lang_instruction = "You MUST respond in Japanese (日本語)."

        return (
            "--- AGENTIC MODE (Phase 5.5) ---\n"
            "You are solving a complex task step by step.\n\n"
            f"YOUR PLAN:\n{plan_text}\n\n"
            "RULES:\n"
            f"0. LANGUAGE: {lang_instruction} Do not switch to other languages.\n"
            "1. Execute ONE step at a time. Call the appropriate tool for the current step.\n"
            "2. Use ONLY these tools: " + tools_list + ". Do NOT invent tool names.\n"
            "3. After each tool result, evaluate what you learned.\n"
            "4. If a step reveals unexpected information, adapt your approach.\n"
            "5. When you have enough information to answer, respond with text (no tool call).\n"
            "6. Keep each tool call focused — prefer one call per step.\n"
            "7. If a step involves writing code or calculating, you MUST use the 'run_python_script' tool if available rather than simulating it.\n"
            f"8. Maximum {MAX_AGENT_ROUNDS} rounds allowed.\n"
        )
