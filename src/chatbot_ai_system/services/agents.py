"""
Specialized Agent Configurations — Phase 10 Orchestrator Upgrade.

Lightweight dataclass configs that define LLM call parameters for each
graph node. These are NOT separate LLM instances — they configure how
the shared provider is called for each node type.
"""

from dataclasses import dataclass, field
from typing import Optional

from chatbot_ai_system.prompts import (
    SYNTHESIS_SYSTEM_PROMPT,
    TOOL_INSTRUCTIONS,
    BASE_SYSTEM_PROMPT,
    GENERAL_PHASE_PROMPT,
    MEDIUM_PHASE_PROMPT,
    COMPLEX_PHASE_PROMPT,
)


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for a specialized agent node in the graph."""

    name: str
    system_prompt: str
    max_tokens: int = 1000
    temperature: float = 0.7
    include_tools: bool = False


# ---------------------------------------------------------------------------
# Predefined Configs
# ---------------------------------------------------------------------------

ROUTER_AGENT = AgentConfig(
    name="router",
    system_prompt="",  # Router uses its own dynamic prompt via build_router_prompt()
    max_tokens=220,
    temperature=0.1,
    include_tools=False,
)

TOOL_EXECUTOR_AGENT = AgentConfig(
    name="tool_executor",
    system_prompt=BASE_SYSTEM_PROMPT + MEDIUM_PHASE_PROMPT + TOOL_INSTRUCTIONS,
    max_tokens=1000,
    temperature=0.3,
    include_tools=True,
)

SYNTHESIS_AGENT = AgentConfig(
    name="synthesis",
    system_prompt=SYNTHESIS_SYSTEM_PROMPT,
    max_tokens=1500,
    temperature=0.7,
    include_tools=False,
)

PLANNER_AGENT = AgentConfig(
    name="planner",
    system_prompt=BASE_SYSTEM_PROMPT + COMPLEX_PHASE_PROMPT,
    max_tokens=500,
    temperature=0.3,
    include_tools=False,
)

REFLECTION_AGENT = AgentConfig(
    name="reflection",
    system_prompt="",  # Reflection uses its own dynamic prompt via build_reflection_prompt()
    max_tokens=300,
    temperature=0.1,
    include_tools=False,
)


# ---------------------------------------------------------------------------
# Agent Registry — maps graph node names to their configs
# ---------------------------------------------------------------------------

# Node name constants (must match orchestrator.py)
_NODE_PLANNER = "planner"
_NODE_TOOL_EXECUTOR = "tool_executor"
_NODE_REFLECTION = "reflection"
_NODE_SYNTHESIS = "synthesis"

AGENT_REGISTRY = {
    _NODE_PLANNER: PLANNER_AGENT,
    _NODE_TOOL_EXECUTOR: TOOL_EXECUTOR_AGENT,
    _NODE_REFLECTION: REFLECTION_AGENT,
    _NODE_SYNTHESIS: SYNTHESIS_AGENT,
}


def get_agent_for_node(node_name: str, phase: str = "GENERAL") -> AgentConfig:
    """
    Select the appropriate AgentConfig for a graph node.

    For the tool_executor node, the phase determines the system prompt:
    - COMPLEX → COMPLEX_PHASE_PROMPT
    - MEDIUM  → MEDIUM_PHASE_PROMPT
    - GENERAL → GENERAL_PHASE_PROMPT
    """
    agent = AGENT_REGISTRY.get(node_name)
    if agent is None:
        return TOOL_EXECUTOR_AGENT  # safe fallback

    # For tool_executor, build phase-specific prompt
    if node_name == _NODE_TOOL_EXECUTOR:
        phase_prompt = {
            "COMPLEX": COMPLEX_PHASE_PROMPT,
            "MEDIUM": MEDIUM_PHASE_PROMPT,
        }.get(phase, GENERAL_PHASE_PROMPT)

        return AgentConfig(
            name=f"tool_executor_{phase.lower()}",
            system_prompt=BASE_SYSTEM_PROMPT + phase_prompt + TOOL_INSTRUCTIONS,
            max_tokens=agent.max_tokens,
            temperature=agent.temperature,
            include_tools=agent.include_tools,
        )

    return agent

