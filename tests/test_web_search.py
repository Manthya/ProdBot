#!/usr/bin/env python3
"""
Standalone test script for DuckDuckGoSearchTool.
Verifies connection and result accuracy.

Usage:
    export PYTHONPATH=$PYTHONPATH:$(pwd)/src
    ./.venv/bin/python3 scripts/test_web_search.py
"""

import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from chatbot_ai_system.tools.implementations.web_search import DuckDuckGoSearchTool
except ImportError:
    print("Error: Could not import DuckDuckGoSearchTool. Ensure src is in PYTHONPATH.")
    sys.exit(1)

async def test_search(tool, query, label):
    print(f"\n--- Testing Query ({label}): '{query}' ---")
    try:
        # Use a region for better results
        result = await tool.run(query=query)
        print("RESULT:")
        print(result)
        
        if "Error" in result or "failed" in result.lower():
            print(f"FAIL: Search returned an error.")
            return False
            
        if "No results found" in result:
            print(f"FAIL: No results found.")
            return False
            
        print(f"PASS: Successfully retrieved results.")
        return True
    except Exception as e:
        print(f"EXCEPTION: {e}")
        return False

async def main():
    print("🚀 Initializing DuckDuckGoSearchTool...")
    tool = DuckDuckGoSearchTool()
    
    queries = [
        ("Current Events", "latest tech news today"),
        ("Factual", "who won the super bowl in 2024?"),
        ("Technical", "how to implement a rag pipeline with langchain and ollama")
    ]
    
    all_passed = True
    for label, query in queries:
        success = await test_search(tool, query, label)
        if not success:
            all_passed = False
            
    print("\n" + "="*40)
    if all_passed:
        print("✅ ALL TESTS COMPLETED SUCCESSFULLY")
    else:
        print("⚠️ SOME TESTS HAD ISSUES (Check relevance)")
    print("="*40)

if __name__ == "__main__":
    asyncio.run(main())
