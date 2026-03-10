from typing import Any, Dict, List, Optional

from .base import MCPTool
from .implementations.python_sandbox import LocalPythonSandbox
from .implementations.web_search import DuckDuckGoSearchTool
from .system_tools import GetCurrentTimeTool

try:
    from .mcp_client import MCPClient
except ImportError:
    MCPClient = None


class RemoteMCPTool(MCPTool):
    """Wrapper for a tool provided by a remote MCP server."""

    def __init__(self, client: "MCPClient", name: str, description: str, schema: Dict[str, Any]):
        self.client = client
        self.name = name
        self.description = description
        self.schema = schema
        self.args_schema = None  # Not used for remote tools

    def to_ollama_format(self) -> Dict[str, Any]:
        """Convert tool definition to Ollama/OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    async def run(self, **kwargs) -> Any:
        """Execute the tool on the remote server."""
        return await self.client.call_tool(self.name, kwargs)


class ToolRegistry:
    """Registry to manage available tools."""

    def __init__(self):
        self._tools: Dict[str, MCPTool] = {}
        self._mcp_clients: List["MCPClient"] = []
        self._remote_tools_cache: Dict[str, RemoteMCPTool] = {}

        # Phase 6.5: Register Free Tools automatically
        self.register(DuckDuckGoSearchTool())
        self.register(LocalPythonSandbox())
        self.register(GetCurrentTimeTool())

    def register(self, tool: MCPTool):
        """Register a new local tool."""
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name} already registered")
        self._tools[tool.name] = tool

    def register_mcp_client(self, client: "MCPClient"):
        """Register an MCP client."""
        self._mcp_clients.append(client)

    def get_tool(self, name: str) -> MCPTool:
        """Get a tool by name (checks local first, then cached remote)."""
        if name in self._tools:
            return self._tools[name]

        if name in self._remote_tools_cache:
            return self._remote_tools_cache[name]

        raise ValueError(f"Tool {name} not found")

    def get_all_tools(self) -> List[MCPTool]:
        """Get all registered tools (local + cached remote)."""
        return list(self._tools.values()) + list(self._remote_tools_cache.values())

    async def refresh_remote_tools(self):
        """Refresh tools from all registered MCP clients."""
        self._remote_tools_cache.clear()

        for client in self._mcp_clients:
            try:
                tool_list = await client.list_tools()
                for tool_def in tool_list:
                    # tool_def is {'type': 'function', 'function': {'name': ..., ...}}
                    func = tool_def.get("function", {})
                    name = func.get("name")
                    if name:
                        remote_tool = RemoteMCPTool(
                            client=client,
                            name=name,
                            description=func.get("description", ""),
                            schema=func.get("parameters", {}),
                        )
                        self._remote_tools_cache[name] = remote_tool
            except Exception as e:
                # Log error but continue
                print(f"Error fetching tools from client: {e}")

    def get_categories(self) -> List[str]:
        """Get list of available tool categories (MCP client names + GENERAL)."""
        categories = ["GENERAL"]
        for client in self._mcp_clients:
            if client.name.upper() not in categories:
                categories.append(client.name.upper())
        return categories

    def get_tools_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get tools belonging to a specific category (client name)."""
        category = category.upper()
        tools = []

        # General tools (local)
        if category == "GENERAL":
            for tool in self._tools.values():
                tools.append(tool.to_ollama_format())
            return tools

        # MCP tools
        for name, tool in self._remote_tools_cache.items():
            if isinstance(tool, RemoteMCPTool) and tool.client.name.upper() == category:
                tools.append(tool.to_ollama_format())

        return tools

    async def get_ollama_tools(self, query: Optional[str] = None) -> List[Dict]:
        """Get filtered tools in Ollama format based on query."""
        MAX_TOOLS = 8  # Increased for agentic mode

        if not query:
            return []

        q = query.lower()
        filtered = []
        seen = set()

        # 1. Dynamic Category Matching
        # If query mentions a category name (e.g. "git", "postgres"), prioritize its tools
        categories = self.get_categories()
        priority_tools = []

        for cat in categories:
            if cat == "GENERAL":
                continue
            # Check if category name is in query
            if cat.lower() in q:
                cat_tools = self.get_tools_by_category(cat)
                priority_tools.extend(cat_tools)

        # 2. Keyword Matching (Fallback/Supplement)
        # We still need some keyword matching for intents that don't match client name exactly
        # e.g. "file" -> FILESYSTEM (if client is named "filesystem")
        # For now, we assume client names are descriptive enough (filesystem, git, fetch)

        # Also include GENERAL tools always if relevant?
        # Actually, let's include tools whose names match keywords in query
        all_tools = list(self._tools.values()) + list(self._remote_tools_cache.values())

        keyword_matches = []
        tokens = q.split()
        for tool in all_tools:
            name = tool.name.lower()
            if any(t in name for t in tokens) or tool.name in [
                t["function"]["name"] for t in priority_tools
            ]:
                continue  # Already added or will be added

            # Simple keyword heuristic
            if any(k in q for k in ["read", "view", "cat", "show"]) and "read" in name:
                keyword_matches.append(tool.to_ollama_format())
            elif any(k in q for k in ["write", "create", "save"]) and "write" in name:
                keyword_matches.append(tool.to_ollama_format())
            elif any(k in q for k in ["search", "find", "grep"]) and "search" in name:
                keyword_matches.append(tool.to_ollama_format())
            elif any(k in q for k in ["list", "dir", "ls"]) and (
                "list" in name or "ls" in name or "dir" in name
            ):
                keyword_matches.append(tool.to_ollama_format())
            elif (
                any(k in q for k in ["git", "status", "diff", "branch", "commit"]) and "git" in name
            ):
                keyword_matches.append(tool.to_ollama_format())

        # Combine: Priority (Category match) > Keyword match
        for tool in priority_tools + keyword_matches:
            name = tool["function"]["name"]
            if name not in seen:
                filtered.append(tool)
                seen.add(name)

        return filtered[:MAX_TOOLS]
