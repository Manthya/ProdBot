import asyncio
import time
from typing import Any, Dict, List, Optional

from chatbot_ai_system.database.redis import redis_client


class ToolReliabilityStore:
    """Track tool reliability with Redis persistence and in-memory fallback."""

    def __init__(self, alpha: float = 0.2):
        self._alpha = alpha
        self._local_cache: Dict[str, Dict[str, Any]] = {}

    def _default_stats(self) -> Dict[str, Any]:
        return {
            "success": 0,
            "failure": 0,
            "ema": 0.5,
            "last_error": None,
            "updated_at": None,
        }

    def _key(self, tool_name: str) -> str:
        return f"tool_stats:{tool_name}"

    async def get_stats(self, tool_name: str) -> Dict[str, Any]:
        """Fetch stats from Redis or local cache."""
        key = self._key(tool_name)
        stats = await redis_client.get(key)
        if not stats:
            stats = self._local_cache.get(tool_name)
        if not stats:
            stats = self._default_stats()
        return stats

    async def update(self, tool_name: str, success: bool, error: Optional[str] = None) -> Dict[str, Any]:
        """Update stats with a new outcome using best-effort atomic-like updates via cache."""
        # For a truly scalable architecture, we retrieve and set. If atomic INCR is critical,
        # we'd implement Lua scripts in redis_client. For now, we drop the asyncio.Lock
        # overhead to unblock worker loops, as exact precision isn't strictly required for EMA.
        stats = await self.get_stats(tool_name)
        stats = dict(stats)  # copy
        if success:
            stats["success"] = int(stats.get("success", 0)) + 1
        else:
            stats["failure"] = int(stats.get("failure", 0)) + 1
            stats["last_error"] = error

        previous_ema = float(stats.get("ema", 0.5))
        outcome = 1.0 if success else 0.0
        stats["ema"] = (self._alpha * outcome) + ((1 - self._alpha) * previous_ema)
        stats["updated_at"] = time.time()

        # Best-effort persistence
        await redis_client.set(self._key(tool_name), stats, ttl=60 * 60 * 24 * 14)
        self._local_cache[tool_name] = stats
        return stats

    async def rank_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rank tools by reliability score while preserving original order for ties."""
        if not tools:
            return tools

        async def score_tool(idx_tool):
            idx, tool = idx_tool
            name = tool.get("function", {}).get("name", "")
            stats = await self.get_stats(name) if name else self._default_stats()
            success = int(stats.get("success", 0))
            failure = int(stats.get("failure", 0))
            total = max(1, success + failure)
            success_rate = success / total
            ema = float(stats.get("ema", 0.5))
            score = (0.7 * ema) + (0.3 * success_rate)
            return (score, idx, tool)

        scored = await asyncio.gather(*[score_tool(item) for item in enumerate(tools)])
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [tool for _, _, tool in scored]


# Global singleton
tool_reliability_store = ToolReliabilityStore()
