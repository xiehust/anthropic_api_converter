from strands import Agent, tool
from strands.models import BedrockModel
import json
import os
import asyncio
import argparse
from strands.tools.mcp import MCPClient
from strands_tools import python_repl
from strands_tools.code_interpreter import AgentCoreCodeInterpreter
from mcp.client.streamable_http import streamable_http_client
from mcp.client.sse import sse_client
from dotenv import load_dotenv
# MCP Client Setup
os.environ["BYPASS_TOOL_CONSENT"]='true'
load_dotenv()
tavily_key = os.environ.get('TAVILY_API_KEY')
# mcp_server MCP Client
mcp_server_client_9538 = MCPClient(
    lambda: streamable_http_client(f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_key}"),
    startup_timeout=30
)


# Agent Configuration
agent_model = BedrockModel(
    model_id="global.anthropic.claude-sonnet-4-6",
    temperature=0.7,
    max_tokens=4000
)

# Main execution
async def main(user_input_arg: str = None, messages_arg: str = None, use_code_exe:bool = False):
    global mcp_server_client_9538

    # Use MCP clients in context managers (only those connected to execution agent)
    with mcp_server_client_9538:
        # Get tools from MCP servers
        mcp_tools = []
        if use_code_exe:
            bedrock_agent_core_code_interpreter = AgentCoreCodeInterpreter(region="us-west-2")
            # code_tool = python_repl
            code_tool = bedrock_agent_core_code_interpreter.code_interpreter
            mcp_tools.extend([code_tool])

        mcp_tools.extend(mcp_server_client_9538.list_tools_sync())

        
        # Create agent with MCP tools
        agent = Agent(
            model=agent_model,
            system_prompt="""You are a helpful AI assistant.""",
            tools=mcp_tools,
            callback_handler=None
        )
        # User input from command-line arguments with priority: --messages > --user-input > default
        if messages_arg is not None and messages_arg.strip():
            # Parse messages JSON and pass full conversation history to agent
            try:
                messages_list = json.loads(messages_arg)
                # Pass the full messages list to the agent
                user_input = messages_list
            except (json.JSONDecodeError, KeyError, TypeError):
                user_input = "Hello, how can you help me?"
        elif user_input_arg is not None and user_input_arg.strip():
            user_input = user_input_arg.strip()
        else:
            # Default fallback when no input provided
            user_input = "Hello, how can you help me?"
        # Execute agent (sync execution)
        result = agent(user_input)
        print(str(result))
        # Access metrics through the AgentResult
        print(f"Total tokens: {result.metrics.accumulated_usage['totalTokens']}")
        print(f"Execution time: {sum(result.metrics.cycle_durations):.2f} seconds")
        print(f"Tools used: {list(result.metrics.tool_metrics.keys())}")

        # Cache metrics (when available)
        if 'cacheReadInputTokens' in result.metrics.accumulated_usage:
            print(f"Cache read tokens: {result.metrics.accumulated_usage['cacheReadInputTokens']}")
        if 'cacheWriteInputTokens' in result.metrics.accumulated_usage:
            print(f"Cache write tokens: {result.metrics.accumulated_usage['cacheWriteInputTokens']}")
        return str(result)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Execute Strands Agent')
    # parser.add_argument('--user-input', type=str, help='User input prompt',default="Search for the current prices of AAPL and GOOGL, then calculate which has a better P/E ratio.")
    # parser.add_argument('--messages', type=str, help='JSON string of conversation messages')
    parser.add_argument('--code-exe', action="store_true")
    args = parser.parse_args()

    user_input_param = 'Please fetch the content at https://httpbin.org/html and find which 3 words have the highest frequency?'
    messages_param = ''
    print(f'user input:{user_input_param}')
    if args.code_exe:
        print(f'python code execution tool is enabled')
    asyncio.run(main(user_input_param, messages_param,use_code_exe=args.code_exe))