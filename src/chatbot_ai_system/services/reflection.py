"""
Reflection Handler — Phase 10 Orchestrator Upgrade.

Catches tool execution errors and JSONDecodeErrors, prompts the LLM to
correct its arguments, and retries up to MAX_RETRIES times before failing
gracefully. Inspired by the Open Interpreter retry pattern.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

from chatbot_ai_system.models.schemas import ChatMessage, MessageRole
from chatbot_ai_system.prompts import build_reflection_prompt
from chatbot_ai_system.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class ReflectionHandler:
    """
    Handles tool call failures by prompting the LLM to self-correct.

    Flow:
    1. Receives a failed tool call (name, args, error)
    2. Prompts the LLM: "The tool call failed with error: {error}.
       Please correct your arguments and try again."
    3. Parses the corrected tool call from the LLM response
    4. Returns (should_retry, corrected_call) or (False, None) on max retries
    """

    def __init__(self, provider: BaseLLMProvider):
        self.provider = provider

    async def handle_error(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        error: str,
        model: str,
        attempt: int,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Attempt to correct a failed tool call via LLM reflection.

        Args:
            tool_name: Name of the tool that failed
            tool_args: Arguments that were passed to the tool
            error: Error message from the failure
            model: LLM model to use for reflection
            attempt: Current attempt number (1-indexed)

        Returns:
            (should_retry, corrected_call) where corrected_call is a dict
            with 'name' and 'arguments' keys, or None if should not retry.
        """
        if attempt >= MAX_RETRIES:
            logger.warning(
                "Reflection: max retries (%d) reached for tool '%s'. Giving up.",
                MAX_RETRIES,
                tool_name,
            )
            return False, None

        prompt = build_reflection_prompt(
            tool_name=tool_name,
            error=error,
            original_args=json.dumps(tool_args, ensure_ascii=False, default=str),
        )

        try:
            response = await self.provider.complete(
                messages=[
                    ChatMessage(role=MessageRole.USER, content=prompt),
                ],
                model=model,
                max_tokens=300,
                temperature=0.1,
            )

            content = (response.message.content or "").strip()
            corrected = self._parse_corrected_call(content)

            if corrected is None:
                logger.warning(
                    "Reflection: failed to parse corrected tool call (attempt %d/%d)",
                    attempt + 1,
                    MAX_RETRIES,
                )
                return False, None

            # If the LLM says SKIP, don't retry
            if corrected.get("name") == "SKIP":
                logger.info(
                    "Reflection: LLM recommended skipping tool '%s': %s",
                    tool_name,
                    corrected.get("reason", "no reason"),
                )
                return False, None

            logger.info(
                "Reflection: LLM corrected tool call for '%s' (attempt %d/%d)",
                tool_name,
                attempt + 1,
                MAX_RETRIES,
            )
            return True, corrected

        except Exception as e:
            logger.error("Reflection LLM call failed: %s", e)
            return False, None

    def _parse_corrected_call(self, content: str) -> Optional[Dict[str, Any]]:
        """Parse a corrected tool call from LLM reflection response."""
        # Try direct JSON parse
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "name" in parsed:
                return parsed
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code block
        import re

        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, dict) and "name" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass

        # Try to find any JSON object in the text
        match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, dict) and "name" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass

        return None
