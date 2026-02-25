import os
import pytest
from unittest.mock import AsyncMock, patch
from chatbot_ai_system.config.mcp_server_config import get_mcp_servers


def _mock_settings_manager():
    # Keep this test unit-level: no DB/network dependency.
    return patch(
        "chatbot_ai_system.config.mcp_server_config.settings_manager.get_setting",
        new=AsyncMock(return_value=None),
    )


class TestMCPServerConfig:
    @patch.dict(os.environ, {}, clear=True)
    @pytest.mark.asyncio
    async def test_default_servers(self):
        """Test that default servers are returned when no env vars are set."""
        with _mock_settings_manager():
            servers = await get_mcp_servers()
        server_names = [s.name for s in servers]
        
        # Core servers should always be present
        assert "filesystem" in server_names
        assert "time" in server_names
        assert "memory" in server_names
        assert "sequential-thinking" in server_names
        
        # Optional servers should be missing
        assert "github" not in server_names
        assert "slack" not in server_names
        assert "brave-search" not in server_names

    @patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}, clear=True)
    @pytest.mark.asyncio
    async def test_github_server_enabled(self):
        """Test that GitHub server is enabled when token is present."""
        with _mock_settings_manager():
            servers = await get_mcp_servers()
        server_names = [s.name for s in servers]
        
        assert "github" in server_names
        
        # Verify specific config
        github_server = next(s for s in servers if s.name == "github")
        assert github_server.env_vars["GITHUB_TOKEN"] == "fake-token"

    @patch.dict(os.environ, {"BRAVE_API_KEY": "fake-key"}, clear=True)
    @pytest.mark.asyncio
    async def test_brave_search_enabled(self):
        """Test that Brave Search server is enabled when key is present."""
        with _mock_settings_manager():
            servers = await get_mcp_servers()
        server_names = [s.name for s in servers]
        
        assert "brave-search" in server_names

    @patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-token", "SLACK_TEAM_ID": "T123"}, clear=True)
    @pytest.mark.asyncio
    async def test_slack_enabled(self):
        """Test that Slack server is enabled when both tokens are present."""
        with _mock_settings_manager():
            servers = await get_mcp_servers()
        server_names = [s.name for s in servers]
        
        assert "slack" in server_names
