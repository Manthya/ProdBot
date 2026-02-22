import os
from typing import Dict, List, Optional, Any
from chatbot_ai_system.config.settings_manager import settings_manager


class MCPServerConfig:
    """Configuration for an MCP server."""

    def __init__(
        self,
        name: str,
        command: str,
        args: List[str],
        env_vars: Optional[Dict[str, str]] = None,
        required_env_vars: Optional[List[str]] = None,
    ):
        self.name = name
        self.command = command
        self.args = args
        self.env_vars = env_vars or {}
        self.required_env_vars = required_env_vars or []


    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "args": self.args,
            "env_vars": self.env_vars,
            "required_env_vars": self.required_env_vars
        }

async def get_mcp_servers() -> List[MCPServerConfig]:
    """
    Get the list of configured MCP servers.
    Checks environment variables to enable/disable servers.
    """
    servers = []

    # Base environment for all servers
    base_env = os.environ.copy()

    # --- Core System ---

    # Filesystem: Restrict to current working directory or a specific path
    # Default to current working directory for safety if not specified
    servers.append(
        MCPServerConfig(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", os.getcwd()],
        )
    )

    # Time
    servers.append(MCPServerConfig(name="time", command="npx", args=["-y", "@mcpcentral/mcp-time"]))

    # Memory (Knowledge Graph)
    servers.append(
        MCPServerConfig(
            name="memory", command="npx", args=["-y", "@modelcontextprotocol/server-memory"]
        )
    )

    # BlackBerry Memory (Wait, no, knowledge graph)

    # PostgreSQL
    if os.environ.get("DATABASE_URL"):
        servers.append(
            MCPServerConfig(
                name="postgres",
                command="npx",
                args=[
                    "-y",
                    "@modelcontextprotocol/server-postgres",
                    os.environ.get("DATABASE_URL"),
                ],
                required_env_vars=["DATABASE_URL"],
            )
        )

    # --- Researcher ---

    # Brave Search
    if os.environ.get("BRAVE_API_KEY"):
        servers.append(
            MCPServerConfig(
                name="brave-search",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-brave-search"],
                env_vars={"BRAVE_API_KEY": os.environ.get("BRAVE_API_KEY")},
                required_env_vars=["BRAVE_API_KEY"],
            )
        )

    # Fetch (Simple HTTP)
    servers.append(MCPServerConfig(name="fetch", command="npx", args=["-y", "zcaceres/fetch-mcp"]))

    # Puppeteer (headful/headless browser)
    servers.append(
        MCPServerConfig(
            name="puppeteer", command="npx", args=["-y", "@modelcontextprotocol/server-puppeteer"]
        )
    )

    # --- Developer ---

    # Git
    servers.append(MCPServerConfig(name="git", command="npx", args=["-y", "@mseep/git-mcp-server"]))

    # GitHub
    if os.environ.get("GITHUB_TOKEN"):
        servers.append(
            MCPServerConfig(
                name="github",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-github"],
                env_vars={"GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN")},
                required_env_vars=["GITHUB_TOKEN"],
            )
        )

    # Docker - requires permission to docker socket
    # We'll assume it's available if we are running locally or strictly configured
    try:
        # Simple check if docker socket exists (linux/mac)
        if os.path.exists("/var/run/docker.sock"):
            servers.append(
                MCPServerConfig(
                    name="docker",
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-docker"],
                )
            )
    except Exception:
        pass

    # E2B Interpreter (Cloud Sandbox)
    if os.environ.get("E2B_API_KEY"):
        servers.append(
            MCPServerConfig(
                name="e2b",
                command="npx",
                args=["-y", "@e2b/mcp-server"],
                env_vars={"E2B_API_KEY": os.environ.get("E2B_API_KEY")},
                required_env_vars=["E2B_API_KEY"],
            )
        )

    # --- Brain ---

    # Sequential Thinking
    servers.append(
        MCPServerConfig(
            name="sequential-thinking",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
        )
    )

    # Sqlite (Analysis)
    # We can point it to a specific DB file if needed, or in-memory
    # For now, let's give it a data.db in the current directory
    servers.append(
        MCPServerConfig(
            name="sqlite", command="npx", args=["-y", "mcp-server-sqlite", "--db", "data.db"]
        )
    )

    # --- Connector ---

    # Slack
    if os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_TEAM_ID"):
        servers.append(
            MCPServerConfig(
                name="slack",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-slack"],
                env_vars={
                    "SLACK_BOT_TOKEN": os.environ.get("SLACK_BOT_TOKEN"),
                    "SLACK_TEAM_ID": os.environ.get("SLACK_TEAM_ID"),
                },
                required_env_vars=["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
            )
        )

    # Google Maps
    if os.environ.get("GOOGLE_MAPS_API_KEY"):
        servers.append(
            MCPServerConfig(
                name="google-maps",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-google-maps"],
                env_vars={"GOOGLE_MAPS_API_KEY": os.environ.get("GOOGLE_MAPS_API_KEY")},
                required_env_vars=["GOOGLE_MAPS_API_KEY"],
            )
        )

    # Sentry
    if os.environ.get("SENTRY_AUTH_TOKEN"):
        servers.append(
            MCPServerConfig(
                name="sentry",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-sentry"],
                env_vars={"SENTRY_AUTH_TOKEN": os.environ.get("SENTRY_AUTH_TOKEN")},
                required_env_vars=["SENTRY_AUTH_TOKEN"],
            )
        )

    # --- User Dynamic Configs ---
    dynamic_configs = await settings_manager.get_setting("mcp_servers")
    if dynamic_configs and isinstance(dynamic_configs, list):
        for cfg in dynamic_configs:
            # Avoid duplicates if name matches built-ins
            if any(s.name == cfg.get("name") for s in servers):
                continue
            
            servers.append(
                MCPServerConfig(
                    name=cfg["name"],
                    command=cfg["command"],
                    args=cfg["args"],
                    env_vars=cfg.get("env_vars"),
                    required_env_vars=cfg.get("required_env_vars")
                )
            )

    return servers
