"""
PTC Agent Streaming Test - Complex multi-tool agent scenario.

Based on notebook5_stream.ipynb from claude_ptc project.
Tests:
1. Container ID retrieval in streaming mode
2. Complex multi-tool agent with PTC
3. Tool execution flow with caller tracking
"""

import json
import time
from typing import Dict, List, Optional, Any
from anthropic import Anthropic
from anthropic.types.beta import BetaTextBlock, BetaToolUseBlock

# Import test configuration
from config import API_KEY, BASE_URL, MODEL_ID

# Import team expense API utilities
from utils.team_expense_api import get_custom_budget, get_expenses, get_team_members


# ============================================================
# Configuration
# ============================================================

# Initialize client
client = Anthropic(api_key=API_KEY, base_url=BASE_URL)


# ============================================================
# Tool Definitions
# ============================================================

# Team expense tools with PTC configuration
EXPENSE_TOOLS = [
    {
        "name": "get_team_members",
        "description": 'Returns a list of team members for a given department. Each team member includes their ID, name, role, level, and contact information. Available departments: engineering, sales, marketing.',
        "input_schema": {
            "type": "object",
            "properties": {
                "department": {
                    "type": "string",
                    "description": "The department name. Case-insensitive.",
                }
            },
            "required": ["department"],
        },
        "allowed_callers": ["code_execution_20250825"],
    },
    {
        "name": "get_expenses",
        "description": "Returns all expense line items for a given employee in a specific quarter. Each expense includes metadata: date, category, amount, status (approved/pending/rejected), etc. Only approved expenses count toward budget limits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string",
                    "description": "The unique employee identifier",
                },
                "quarter": {
                    "type": "string",
                    "description": "Quarter identifier: 'Q1', 'Q2', 'Q3', or 'Q4'",
                },
            },
            "required": ["employee_id", "quarter"],
        },
        "allowed_callers": ["code_execution_20250825"],
    },
    {
        "name": "get_custom_budget",
        "description": 'Get the custom quarterly travel budget for a specific employee. Standard budget is $5,000, but some employees have custom limits.',
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "The unique employee identifier",
                }
            },
            "required": ["user_id"],
        },
        "allowed_callers": ["code_execution_20250825"],
    },
]

# Weather tool (direct call)
WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get current weather information for a city.",
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "The city name (e.g., 'Beijing', 'New York')",
            },
            "units": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "default": "celsius"
            }
        },
        "required": ["city"],
    },
}

# Code execution tool
CODE_EXECUTION_TOOL = {
    "type": "code_execution_20250825",
    "name": "code_execution",
}


# ============================================================
# Tool Functions
# ============================================================

def get_weather(city: str, units: str = "celsius") -> str:
    """Mock weather API"""
    import random

    weather_data = {
        "beijing": {"temp_c": 15, "condition": "Partly Cloudy", "humidity": 45},
        "shanghai": {"temp_c": 22, "condition": "Sunny", "humidity": 60},
        "new york": {"temp_c": 18, "condition": "Cloudy", "humidity": 55},
        "tokyo": {"temp_c": 20, "condition": "Clear", "humidity": 50},
        "paris": {"temp_c": 14, "condition": "Overcast", "humidity": 65},
    }

    city_lower = city.lower().strip()
    data = weather_data.get(city_lower, {
        "temp_c": random.randint(5, 35),
        "condition": random.choice(["Sunny", "Cloudy", "Rainy"]),
        "humidity": random.randint(30, 90)
    })

    if units.lower() == "fahrenheit":
        temp = data["temp_c"] * 9/5 + 32
        temp_unit = "F"
    else:
        temp = data["temp_c"]
        temp_unit = "C"

    return json.dumps({
        "city": city.title(),
        "temperature": f"{temp:.1f}{temp_unit}",
        "condition": data["condition"],
        "humidity": f"{data['humidity']}%"
    })


TOOL_FUNCTIONS = {
    "get_team_members": get_team_members,
    "get_expenses": get_expenses,
    "get_custom_budget": get_custom_budget,
    "get_weather": get_weather,
}


# ============================================================
# PTC Agent with Streaming
# ============================================================

def run_ptc_agent_streaming(
    user_message: str,
    tools: List[Dict],
    max_iterations: int = 10,
    debug: bool = False
) -> Dict[str, Any]:
    """
    Run PTC agent with streaming responses.

    Returns dict with:
    - result: Final text response
    - container_id: Container ID if returned
    - total_tokens: Total tokens used
    - elapsed_time: Total time taken
    - api_calls: Number of API calls made
    - tool_calls: List of tool calls made
    """
    messages = [{"role": "user", "content": user_message}]
    total_tokens = 0
    start_time = time.time()
    container_id = None
    api_counter = 0
    tool_calls_made = []
    final_response = ""

    while api_counter < max_iterations:
        api_counter += 1
        print(f"\n[API Call #{api_counter}] Streaming...")
        print("-" * 60)

        # Variables for collecting response
        response_content = []
        current_text = ""
        current_tool_use = None
        current_tool_input = ""
        stop_reason = None
        input_tokens = 0
        output_tokens = 0

        # Stream using beta API
        # Use extra_body to pass container ID (SDK doesn't natively support container param)
        stream = client.beta.messages.create(
            model=MODEL_ID,
            max_tokens=8000,
            tools=tools,
            messages=messages,
            system="You are a helpful agent that analyzes data using available tools.",
            betas=["advanced-tool-use-2025-11-20"],
            stream=True,
            extra_body={"container": container_id} if container_id else None,
        )

        for event in stream:
            event_type = getattr(event, 'type', None)

            if debug:
                print(f"[DEBUG] {event_type}: {event}")

            if event_type == 'message_start':
                message = getattr(event, 'message', None)
                if message:
                    usage = getattr(message, 'usage', None)
                    if usage:
                        input_tokens = getattr(usage, 'input_tokens', 0)

                    # Check for container ID
                    container = getattr(message, 'container', None)
                    if container:
                        container_id = getattr(container, 'id', None)
                        expires_at = getattr(container, 'expires_at', None)
                        print(f"[Container] ID: {container_id}")
                        if expires_at:
                            print(f"[Container] Expires: {expires_at}")

            elif event_type == 'content_block_start':
                block = getattr(event, 'content_block', None)
                if block:
                    block_type = getattr(block, 'type', None)
                    if block_type == 'text':
                        current_text = ""
                        print("\n[Text] ", end="", flush=True)
                    elif block_type == 'tool_use':
                        tool_input = getattr(block, 'input', {})
                        current_tool_use = {
                            'id': getattr(block, 'id', ''),
                            'name': getattr(block, 'name', ''),
                            'input': tool_input if tool_input else {},
                            'caller': getattr(block, 'caller', None)
                        }
                        current_tool_input = ""
                        print(f"\n[Tool Call] {current_tool_use['name']}")
                        if current_tool_use['input']:
                            print(f"  Input: {current_tool_use['input']}")
                    elif block_type == 'server_tool_use':
                        # Capture server_tool_use for conversation history
                        server_tool = {
                            'type': 'server_tool_use',
                            'id': getattr(block, 'id', ''),
                            'name': getattr(block, 'name', ''),
                            'input': getattr(block, 'input', {})
                        }
                        response_content.append(server_tool)
                        print(f"\n[Server Tool Use] {server_tool['name']}")

            elif event_type == 'content_block_delta':
                delta = getattr(event, 'delta', None)
                if delta:
                    delta_type = getattr(delta, 'type', None)
                    if delta_type == 'text_delta':
                        text = getattr(delta, 'text', '')
                        current_text += text
                        print(text, end="", flush=True)
                    elif delta_type == 'input_json_delta':
                        partial_json = getattr(delta, 'partial_json', '')
                        if partial_json:
                            current_tool_input += partial_json

            elif event_type == 'content_block_stop':
                if current_text:
                    response_content.append({
                        'type': 'text',
                        'text': current_text
                    })
                    final_response = current_text
                    current_text = ""

                if current_tool_use:
                    final_input = current_tool_use['input']
                    if not final_input and current_tool_input:
                        try:
                            final_input = json.loads(current_tool_input)
                        except json.JSONDecodeError:
                            final_input = {}

                    response_content.append({
                        'type': 'tool_use',
                        'id': current_tool_use['id'],
                        'name': current_tool_use['name'],
                        'input': final_input,
                        'caller': current_tool_use['caller']
                    })
                    current_tool_use = None
                    current_tool_input = ""

            elif event_type == 'message_delta':
                delta = getattr(event, 'delta', None)
                if delta:
                    stop_reason = getattr(delta, 'stop_reason', None)
                usage = getattr(event, 'usage', None)
                if usage:
                    output_tokens = getattr(usage, 'output_tokens', 0)

        print(f"\n" + "-" * 60)
        print(f"[Tokens] Input: {input_tokens:,} | Output: {output_tokens:,}")
        print(f"[Stop Reason] {stop_reason}")
        if container_id:
            print(f"[Container ID] {container_id}")

        total_tokens += input_tokens + output_tokens

        if stop_reason == "end_turn":
            break

        # Handle tool use - execute ALL tool_use blocks client-side
        # In PTC mode, execute_code is handled server-side and NOT returned as tool_use
        if stop_reason == "tool_use":
            # Build assistant content from response
            assistant_content = []
            for item in response_content:
                if item['type'] == 'text':
                    assistant_content.append(BetaTextBlock(type='text', text=item['text']))
                elif item['type'] == 'tool_use':
                    assistant_content.append(BetaToolUseBlock(
                        type='tool_use',
                        id=item['id'],
                        name=item['name'],
                        input=item['input']
                    ))

            messages.append({"role": "assistant", "content": assistant_content})
            tool_results = []

            for item in response_content:
                if item['type'] == 'tool_use':
                    tool_name = item['name']
                    tool_input = item['input']
                    tool_use_id = item['id']
                    caller = item.get('caller')

                    # Track caller type
                    caller_type = "unknown"
                    if caller:
                        caller_type = getattr(caller, 'type', 'unknown')
                        if caller_type == "code_execution_20250825":
                            print(f"[PTC] Tool called from code execution: {tool_name}")
                        elif caller_type == "direct":
                            print(f"[Direct] Tool called by model: {tool_name}")

                    print(f"\n[Executing Tool] {tool_name}")

                    try:
                        if tool_name in TOOL_FUNCTIONS:
                            result = TOOL_FUNCTIONS[tool_name](**tool_input)
                        else:
                            result = f"Error: Unknown tool {tool_name}"
                    except Exception as e:
                        result = f"Error: {str(e)}"

                    if isinstance(result, (dict, list)):
                        content = json.dumps(result)
                    else:
                        content = str(result)

                    preview = content[:150] + "..." if len(content) > 150 else content
                    print(f"[Tool Result] {preview}")

                    tool_calls_made.append({
                        "name": tool_name,
                        "input": tool_input,
                        "caller_type": caller_type
                    })

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                    })

            # Only append tool_results if we have any
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            print(f"\n[Unexpected Stop Reason] {stop_reason}")
            break

    elapsed_time = time.time() - start_time

    return {
        "result": final_response,
        "container_id": container_id,
        "total_tokens": total_tokens,
        "elapsed_time": elapsed_time,
        "api_calls": api_counter,
        "tool_calls": tool_calls_made,
    }


# ============================================================
# Test Cases
# ============================================================

def test_simple_weather_query():
    """Test 1: Simple weather query with direct tool call"""
    print("\n" + "=" * 70)
    print("TEST 1: Simple Weather Query (Direct Tool Call)")
    print("=" * 70)

    tools = [WEATHER_TOOL, CODE_EXECUTION_TOOL]
    query = "What is the weather in Beijing?"

    result = run_ptc_agent_streaming(query, tools)

    print("\n" + "=" * 70)
    print("[Test Results]")
    print(f"  Final Response: {result['result'][:200]}...")
    print(f"  Container ID: {result['container_id']}")
    print(f"  Total Tokens: {result['total_tokens']:,}")
    print(f"  Elapsed Time: {result['elapsed_time']:.2f}s")
    print(f"  API Calls: {result['api_calls']}")
    print(f"  Tool Calls: {len(result['tool_calls'])}")

    # Verify container ID is returned
    if result['container_id']:
        print("  [PASS] Container ID returned")
    else:
        print("  [INFO] No container ID (may be direct tool call)")

    return result


def test_complex_expense_analysis():
    """Test 2: Complex expense analysis with PTC and multiple tool calls"""
    print("\n" + "=" * 70)
    print("TEST 2: Complex Expense Analysis (PTC Multi-Tool)")
    print("=" * 70)

    tools = EXPENSE_TOOLS + [CODE_EXECUTION_TOOL]
    query = """Which engineering team members exceeded their Q3 travel budget?
    Standard quarterly travel budget is $5,000.
    However, some employees have custom budget limits.
    For anyone who exceeded the $5,000 standard budget, check if they have a custom budget exception."""

    result = run_ptc_agent_streaming(query, tools)

    print("\n" + "=" * 70)
    print("[Test Results]")
    print(f"  Final Response: {result['result'][:300]}...")
    print(f"  Container ID: {result['container_id']}")
    print(f"  Total Tokens: {result['total_tokens']:,}")
    print(f"  Elapsed Time: {result['elapsed_time']:.2f}s")
    print(f"  API Calls: {result['api_calls']}")
    print(f"  Tool Calls Made: {len(result['tool_calls'])}")

    # Count tool call types
    ptc_calls = sum(1 for tc in result['tool_calls'] if tc['caller_type'] == 'code_execution_20250825')
    direct_calls = sum(1 for tc in result['tool_calls'] if tc['caller_type'] == 'direct')
    print(f"    - PTC calls: {ptc_calls}")
    print(f"    - Direct calls: {direct_calls}")

    # Verify container ID is returned for PTC
    if result['container_id']:
        print("  [PASS] Container ID returned")
    else:
        print("  [WARN] No container ID returned")

    return result


def test_container_id_streaming():
    """Test 3: Verify container ID is properly returned in streaming mode"""
    print("\n" + "=" * 70)
    print("TEST 3: Container ID in Streaming Mode")
    print("=" * 70)

    tools = [CODE_EXECUTION_TOOL]
    query = "Use Python to calculate the sum of numbers from 1 to 10 and print the result."

    # Track container from message_start event
    container_from_start = None

    with client.beta.messages.stream(
        model=MODEL_ID,
        max_tokens=4096,
        tools=tools,
        messages=[{"role": "user", "content": query}],
        betas=["code-execution-2025-08-25"],
    ) as stream:
        for event in stream:
            event_type = type(event).__name__

            if event_type == "BetaRawMessageStartEvent":
                msg = event.message
                print(f"[message_start]")
                print(f"  message.id: {msg.id}")
                print(f"  message.container: {msg.container}")
                if hasattr(msg, 'container') and msg.container:
                    container_from_start = msg.container.id
                    print(f"  [PASS] Container ID: {container_from_start}")
                    print(f"  [PASS] Expires at: {msg.container.expires_at}")

        final = stream.get_final_message()
        print(f"\n[Final Message]")
        print(f"  stop_reason: {final.stop_reason}")
        print(f"  content blocks: {len(final.content)}")

    print("\n" + "=" * 70)
    print("[Test Results]")
    if container_from_start:
        print(f"  [PASS] Container ID successfully retrieved: {container_from_start}")
    else:
        print(f"  [FAIL] Container ID not found in message_start event")

    return {"container_id": container_from_start}


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 70)
    print("PTC Agent Streaming Test Suite")
    print("=" * 70)
    print(f"API Base URL: {BASE_URL}")
    print(f"Model: {MODEL_ID}")
    print("=" * 70)

    # Run tests
    results = {}

    # Test 1: Simple query
    results['test1'] = test_simple_weather_query()

    # Test 2: Complex PTC agent
    results['test2'] = test_complex_expense_analysis()

    # Test 3: Container ID verification
    results['test3'] = test_container_id_streaming()

    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Test 1 (Simple Weather): Container={results['test1'].get('container_id', 'None')}")
    print(f"Test 2 (Complex PTC): Container={results['test2'].get('container_id', 'None')}, Tools={len(results['test2'].get('tool_calls', []))}")
    print(f"Test 3 (Container Check): Container={results['test3'].get('container_id', 'None')}")
    print("=" * 70)
    print("All tests completed!")
