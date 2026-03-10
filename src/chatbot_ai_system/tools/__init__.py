from .base import MCPTool
from .registry import ToolRegistry
from .system_tools import CheckRepoStatusTool, GetCurrentTimeTool

# Global registry instance
registry = ToolRegistry()

# Register default tools safely
for tool in [GetCurrentTimeTool(), CheckRepoStatusTool()]:
    if tool.name not in registry._tools:
        registry.register(tool)
