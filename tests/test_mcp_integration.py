
import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), 'src'))

from chatbot_ai_system.tools.registry import ToolRegistry
from chatbot_ai_system.tools.mcp_client import MCPClient

async def main():
    registry = ToolRegistry()
    
    # Path to our mock server script
    server_script = os.path.join(os.getcwd(), "tests", "mock_mcp_server.py")
    
    print(f"Starting MCP client for {server_script}...")
    client = MCPClient(
        name="mock_server",
        command="python",
        args=[server_script],
        env=os.environ.copy()
    )
    
    registry.register_mcp_client(client)
    
    try:
        print("Refreshing tools from MCP...")
        await registry.refresh_remote_tools()
        
        print("Fetching tools for 'add' query...")
        # First call might take a moment to connect
        tools = await registry.get_ollama_tools(query="add numbers")
        print(f"Found {len(tools)} tools matching query.")
        
        # Verify 'add' tool is present
        add_tool = next((t for t in tools if t['function']['name'] == 'add'), None)
        if add_tool:
            print("Verified: 'add' tool found.")
            print(f"Tool description: {add_tool['function']['description']}")
        else:
            print("FAILED: 'add' tool NOT found.")
            import json
            print("Available tools:", json.dumps(tools, indent=2))
            return
            
        # Execute 'add' tool
        print("Executing 'add' tool...")
        tool_instance = registry.get_tool("add")
        result = await tool_instance.run(a=10, b=20)
        print(f"Result: {result}")
        
        # Result might be string or whatever MCP returns
        if str(result).strip() == "30":
             print("Verified: 'add' tool returned correct result.")
        else:
             print(f"FAILED: 'add' tool returned unexpected result: {result}")

    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()

if __name__ == "__main__":
    print("Running MCP Integration Test...")
    asyncio.run(main())
