"""
Centralized Prompt Definitions — Phase 10 Orchestrator Upgrade.

All system prompts, router prompts, and synthesis prompts live here.
Modules import from this file instead of hardcoding prompt strings inline.
"""

# ---------------------------------------------------------------------------
# Router Prompt (used by ChatOrchestrator._build_router_prompt)
# ---------------------------------------------------------------------------
ROUTER_SYSTEM_PROMPT_TEMPLATE = (
    "You are a routing classifier for a local assistant. Output ONLY JSON, no prose.\n"
    "Keys:\n"
    "  phase: GENERAL | MEDIUM | COMPLEX\n"
    "  tool_required: true/false\n"
    "  tool_domains: array of tool domains from this list: {domain_list}\n"
    "  expected_tool_calls: 0 | 1 | 2\n"
    "  confidence: 0.0 - 1.0\n"
    "  need_clarification: true/false\n\n"
    "Guidelines:\n"
    "- GENERAL: knowledge-only or casual chat\n"
    "- MEDIUM: one tool call or short single-step answer\n"
    "- COMPLEX: multi-step with dependencies\n"
    "- tool_domains must be empty if tool_required=false\n"
)

ROUTER_STRICT_SYSTEM_PROMPT = "Output ONLY a JSON object for routing. No prose."

# ---------------------------------------------------------------------------
# Intent Classifier Prompt (used by ChatOrchestrator._classify_intent)
# ---------------------------------------------------------------------------
INTENT_CLASSIFIER_PROMPT = (
    "You are an intent classifier. Analyze the user's request.\n"
    "Categories:\n"
    "1. GIT: Version control, commits, branches, diffs.\n"
    "2. FILESYSTEM: Reading/writing files, listing directories, searching.\n"
    "3. FETCH: Web requests, extracting content from URLs.\n"
    "4. GENERAL: General knowledge, coding advice (without file access), greetings.\n"
    "Output ONLY the category name (e.g., 'GIT')."
)

# ---------------------------------------------------------------------------
# Phase-specific System Prompts (used by ChatOrchestrator._get_system_prompt)
# ---------------------------------------------------------------------------
BASE_SYSTEM_PROMPT = "You are a helpful AI assistant."

GENERAL_PHASE_PROMPT = (
    "\nBe concise and direct. Ask a clarifying question if the request is ambiguous. "
    "Admit uncertainty when needed. Do not mention tools."
)

MEDIUM_PHASE_PROMPT = (
    "\nPrefer a single-step solution. If tools are needed, call at most one tool. "
    "After using a tool, answer plainly and briefly."
)

COMPLEX_PHASE_PROMPT = (
    "\nHandle multi-step tasks carefully. Use tools when required and verify outputs "
    "before concluding."
)

NO_TOOLS_PROMPT = "\nAnswer using your internal knowledge."

TOOL_INSTRUCTIONS = (
    "\nYou have access to external tools via MCP.\n"
    "1. If the user's request requires it, call the appropriate tool.\n"
    "2. Output a valid JSON tool call.\n"
    "3. Use tool results as the source of truth.\n"
    "4. Never claim an action/result unless a tool output in this conversation proves it.\n"
    "5. Never simulate, assume, or fabricate execution."
)

# ---------------------------------------------------------------------------
# Verification Prompt (used by ChatOrchestrator._verify_tool_result)
# ---------------------------------------------------------------------------
VERIFICATION_PROMPT_TEMPLATE = (
    "You are a verification assistant. Given the user request and tool result, "
    "decide if the result is sufficient to answer the user.\n"
    'Output ONLY JSON: {{"ok": true/false, "reason": "..."}}\n\n'
    "User request: {user_input}\n"
    "Tool: {tool_name}\n"
    "Tool args: {tool_args}\n"
    "Tool result: {excerpt}\n"
)

# ---------------------------------------------------------------------------
# Synthesis Prompt (new — for the graph synthesis node)
# ---------------------------------------------------------------------------
SYNTHESIS_SYSTEM_PROMPT = (
    "You are a helpful AI assistant generating a final response.\n"
    "Rules:\n"
    "1. Base your answer ONLY on the tool results provided in this conversation.\n"
    "2. Format your response in clean Markdown when appropriate.\n"
    "3. Be concise but complete.\n"
    "4. Never simulate, assume, or fabricate information.\n"
    "5. If tool results are insufficient, say so honestly."
)

# ---------------------------------------------------------------------------
# Reflection Prompt (new — for the reflection/retry node)
# ---------------------------------------------------------------------------
REFLECTION_PROMPT_TEMPLATE = (
    "The tool call '{tool_name}' failed with the following error:\n"
    "{error}\n\n"
    "Original arguments: {original_args}\n\n"
    "Please analyze the error and output a corrected tool call as JSON:\n"
    '{{"name": "<tool_name>", "arguments": {{...}}}}\n\n'
    "If the error is unrecoverable (e.g., tool does not exist), output:\n"
    '{{"name": "SKIP", "reason": "..."}}'
)

# ---------------------------------------------------------------------------
# Forced Tool-Call Prompt (used in Phase 6d retry)
# ---------------------------------------------------------------------------
FORCED_TOOL_CALL_PROMPT = (
    "You must call exactly one available tool now. "
    "Return a tool call only. Do not provide natural language."
)

# ---------------------------------------------------------------------------
# Summarization Prompts (used by ChatOrchestrator._summarize_conversation)
# ---------------------------------------------------------------------------
SUMMARIZE_PROMPT_TEMPLATE = (
    "Summarize the following conversation segment efficiently. "
    "Focus on key facts, user preferences, and important decisions. "
    "Do not lose important details.\n\n"
    "{text}"
)

CONSOLIDATE_SUMMARY_PROMPT_TEMPLATE = (
    "Here is the previous conversation summary:\n"
    "{old_summary}\n\n"
    "Here is the new conversation segment:\n"
    "{new_summary}\n\n"
    "Create a consolidated summary of the entire conversation. Keep it concise."
)


def build_router_prompt(domain_list: str) -> str:
    """Format the router system prompt with available domains."""
    return ROUTER_SYSTEM_PROMPT_TEMPLATE.format(domain_list=domain_list)


def build_system_prompt(phase: str, has_tools: bool) -> str:
    """Build the appropriate system prompt based on phase and tool availability."""
    prompt = BASE_SYSTEM_PROMPT

    phase = phase.upper()
    if phase == "GENERAL":
        prompt += GENERAL_PHASE_PROMPT
    elif phase == "MEDIUM":
        prompt += MEDIUM_PHASE_PROMPT
    else:
        prompt += COMPLEX_PHASE_PROMPT

    if not has_tools:
        return prompt + NO_TOOLS_PROMPT

    return prompt + TOOL_INSTRUCTIONS


def build_verification_prompt(
    user_input: str, tool_name: str, tool_args: str, excerpt: str
) -> str:
    """Format the verification prompt with specific tool call details."""
    return VERIFICATION_PROMPT_TEMPLATE.format(
        user_input=user_input,
        tool_name=tool_name,
        tool_args=tool_args,
        excerpt=excerpt,
    )


def build_reflection_prompt(tool_name: str, error: str, original_args: str) -> str:
    """Format the reflection prompt for retry logic."""
    return REFLECTION_PROMPT_TEMPLATE.format(
        tool_name=tool_name,
        error=error,
        original_args=original_args,
    )
