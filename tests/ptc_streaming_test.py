from anthropic import Anthropic

# Import test configuration
from config import API_KEY, BASE_URL, MODEL_ID

# Initialize the client
client = Anthropic(api_key=API_KEY, base_url=BASE_URL)

# Define PTC tools - external tool called from code
tools = [
    {
        "type": "code_execution_20250825",
        "name": "code_execution"
    },
    {
        "name": "get_weather",
        "description": "Get the current weather for a location. Returns temperature in Celsius.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name"}
            },
            "required": ["location"]
        },
        "allowed_callers": ["code_execution_20250825"]
    }
]

# Test: Non-streaming PTC - simple code that calls external tool
print("=" * 60)
print("Test 1: Non-streaming PTC (code calling external tool)")
print("=" * 60)

response1 = client.beta.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["advanced-tool-use-2025-11-20"],
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": "Write Python code to get the weather in Tokyo using the get_weather tool, then print the result."
    }],
    tools=tools
)

print(f"Stop reason: {response1.stop_reason}")
print(f"Content blocks: {len(response1.content)}")
for i, block in enumerate(response1.content):
    block_type = getattr(block, 'type', 'unknown')
    print(f"  Block {i}: {block_type}")
    if block_type == "tool_use":
        print(f"    name: {getattr(block, 'name', '')}")
        print(f"    caller: {getattr(block, 'caller', None)}")
    elif block_type == "server_tool_use":
        print(f"    name: {getattr(block, 'name', '')}")
        print(f"    input: {getattr(block, 'input', {})}")

# Test: Streaming PTC - simple code that calls external tool
print("\n" + "=" * 60)
print("Test 2: Streaming PTC (code calling external tool)")
print("=" * 60)

with client.beta.messages.stream(
    model="claude-sonnet-4-5-20250929",
    betas=["advanced-tool-use-2025-11-20"],
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": "Write Python code to get the weather in Paris using the get_weather tool, then print the result."
    }],
    tools=tools
) as stream:
    print("\nStreaming events:")
    event_counts = {}
    container_id = None
    for event in stream:
        event_type = type(event).__name__
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

        # Check for container info in message_start
        if event_type == "BetaRawMessageStartEvent":
            print(f"  [message_start event] {event}")
            # Check the raw event data
            if hasattr(event, 'message') and event.message:
                print(f"  [message] {event.message}")
                if hasattr(event.message, 'container') and event.message.container:
                    container_id = event.message.container.id
                    print(f"  [Container ID from message_start] {container_id}")

    print("  Event counts:")
    for evt, count in sorted(event_counts.items()):
        print(f"    {evt}: {count}")

    final_message = stream.get_final_message()
    print(f"\nFinal stop_reason: {final_message.stop_reason}")
    print(f"Final content blocks: {len(final_message.content)}")
    for i, block in enumerate(final_message.content):
        block_type = getattr(block, 'type', 'unknown')
        print(f"  Block {i}: {block_type}")
        if block_type == "tool_use":
            print(f"    name: {getattr(block, 'name', '')}")
            print(f"    input: {getattr(block, 'input', {})}")
            print(f"    caller: {getattr(block, 'caller', None)}")
        elif block_type == "server_tool_use":
            print(f"    name: {getattr(block, 'name', '')}")
            print(f"    input: {getattr(block, 'input', {})}")

print("\n" + "=" * 60)
print("PTC Streaming Test Complete!")
print("=" * 60)
