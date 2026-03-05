"""
Tests for Phase 10.1 Multi-Agent Handoff.

Validates that get_agent_for_node correctly maps graph nodes to their specialized AgentConfig,
and handles dynamic system prompt substitution based on the request phase.
"""

import pytest
from chatbot_ai_system.services.agents import (
    get_agent_for_node,
    _NODE_PLANNER,
    _NODE_TOOL_EXECUTOR,
    _NODE_REFLECTION,
    _NODE_SYNTHESIS,
    PLANNER_AGENT,
    REFLECTION_AGENT,
    SYNTHESIS_AGENT,
)
from chatbot_ai_system.prompts import (
    COMPLEX_PHASE_PROMPT,
    MEDIUM_PHASE_PROMPT,
    GENERAL_PHASE_PROMPT,
)


def test_agent_registry_mapping():
    """Test static mappings for nodes that don't depend on phase."""
    assert get_agent_for_node(_NODE_PLANNER).name == PLANNER_AGENT.name
    assert get_agent_for_node(_NODE_REFLECTION).name == REFLECTION_AGENT.name
    assert get_agent_for_node(_NODE_SYNTHESIS).name == SYNTHESIS_AGENT.name


def test_tool_executor_phase_prompts():
    """Test that tool_executor dynamically picks the right prompt based on phase."""
    # COMPLEX phase
    complex_agent = get_agent_for_node(_NODE_TOOL_EXECUTOR, phase="COMPLEX")
    assert complex_agent.name == "tool_executor_complex"
    assert COMPLEX_PHASE_PROMPT in complex_agent.system_prompt
    assert MEDIUM_PHASE_PROMPT not in complex_agent.system_prompt
    assert complex_agent.include_tools is True

    # MEDIUM phase
    medium_agent = get_agent_for_node(_NODE_TOOL_EXECUTOR, phase="MEDIUM")
    assert medium_agent.name == "tool_executor_medium"
    assert MEDIUM_PHASE_PROMPT in medium_agent.system_prompt
    assert COMPLEX_PHASE_PROMPT not in medium_agent.system_prompt
    assert medium_agent.include_tools is True

    # GENERAL phase (default)
    general_agent = get_agent_for_node(_NODE_TOOL_EXECUTOR, phase="GENERAL")
    assert general_agent.name == "tool_executor_general"
    assert GENERAL_PHASE_PROMPT in general_agent.system_prompt
    assert COMPLEX_PHASE_PROMPT not in general_agent.system_prompt


def test_invalid_node_fallback():
    """Test fallback behavior for unrecognized nodes."""
    fallback = get_agent_for_node("invalid_node_name")
    assert fallback.name == "tool_executor"
    assert fallback.include_tools is True
