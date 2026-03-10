import pytest
import json
from unittest.mock import AsyncMock

from chatbot_ai_system.tools.registry import RemoteMCPTool, ToolRegistry
from chatbot_ai_system.tools.mcp_client import MCPClient


@pytest.fixture
def mcp_tool():
    """Returns a mock RemoteMCPTool instance."""
    mock_client = AsyncMock(spec=MCPClient)
    schema = {
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"}
        },
        "required": ["query"]
    }
    tool = RemoteMCPTool(
        client=mock_client,
        name="test_tool",
        description="A test tool",
        schema=schema
    )
    return tool, mock_client


@pytest.mark.asyncio
async def test_mcp_malformed_json_arguments(mcp_tool):
    """
    Test Malformed JSON handling:
    Simulate LLM returning broken structured JSON or wrong types.
    Assert it fails safely without crashing the client connection.
    """
    tool, mock_client = mcp_tool
    
    # Normally JSON parsing happens in provider `_try_parse_tool_calls`
    # Here we simulate the LLM passing a dict with wrong type (e.g. dict instead of string)
    # The MCP client accepts `Dict[str, Any]` but the schema dictates string.
    # The MCP server will reject it, but the client must handle that rejection gracefully.
    
    malformed_args = {
        "query": {"$ne": 1},  # MongoDB style injection attempt
        "path": None          # Null instead of string
    }
    
    mock_client.call_tool.side_effect = Exception("MCP Server Protocol Error: Invalid arguments")

    with pytest.raises(Exception, match="MCP Server Protocol Error"):
        await tool.run(**malformed_args)
        
    # Verify the client attempted to send it exactly as-is without internal crash
    mock_client.call_tool.assert_called_once_with("test_tool", malformed_args)


@pytest.mark.asyncio
async def test_mcp_directory_traversal_payload(mcp_tool):
    """
    Test Directory Traversal string injection:
    Pass ../../etc/passwd into a path parameter.
    Assert the string is passed verbatim to the MCP server for handling,
    proving out-of-bounds execution is purely dependent on the MCP server sandbox.
    """
    tool, mock_client = mcp_tool
    
    mock_client.call_tool.return_value = "Content of /etc/passwd"  # Simulate a vulnerable server
    
    payload = {
        "query": "find files",
        "path": "../../../../../etc/passwd"
    }
    
    result = await tool.run(**payload)
    
    mock_client.call_tool.assert_called_once_with("test_tool", payload)
    assert result == "Content of /etc/passwd"


@pytest.mark.asyncio
async def test_mcp_sql_injection_payload(mcp_tool):
    """
    Test SQL Injection:
    Pass SQL drop table commands.
    Assert arguments are safely transmitted via JSON RPC.
    """
    tool, mock_client = mcp_tool
    
    mock_client.call_tool.return_value = "Query executed successfully"
    
    sqli_payload = "'; DROP TABLE users; --"
    payload = {"query": sqli_payload}
    
    await tool.run(**payload)
    
    # Assert arguments were never interpreted locally as SQL
    mock_client.call_tool.assert_called_once_with("test_tool", {"query": sqli_payload})


@pytest.mark.asyncio
async def test_mcp_read_only_violation(mcp_tool):
    """
    Test Unauthorized Write (Read-Only bypass):
    If an MCP client is mocked to reject non-GET requests (or the LLM hallucinates
    a 'delete_file' tool on a read-only filesystem client), verify the run fails.
    """
    mock_client = AsyncMock()
    
    # Try to instantiate a tool that wasn't registered
    tool = RemoteMCPTool(client=mock_client, name="delete_system_files", description="Delete", schema={})
    
    mock_client.call_tool.side_effect = Exception("Tool not found: delete_system_files")
    
    with pytest.raises(Exception, match="Tool not found"):
        await tool.run(pattern="*")
        
    mock_client.call_tool.assert_called_once()
