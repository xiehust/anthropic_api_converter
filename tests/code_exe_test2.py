import os
from anthropic import Anthropic

# Initialize the client
client = Anthropic(
    api_key='sk-22b986366e084cafae975331ae994e8a',
    base_url='http://localhost:8002'
)

# First request: Create a file with a random number
response1 = client.beta.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["code-execution-2025-08-25"],
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": "Write and run a Python script that prints Hello World"
    }],
    tools=[{
        "type": "code_execution_20250825",
        "name": "code_execution"
    }]
)

print("\n" + "="*50)
print("Testing Non-Streaming Mode")
print("="*50)

# Extract and verify container ID from non-streaming response
print(f"Response content: {response1.content}")
non_stream_container = getattr(response1, 'container', None)
print(f"\n[Container Test - Non-Streaming]")
print(f"  container: {non_stream_container}")
if non_stream_container:
    print(f"  ✓ Container ID: {non_stream_container.id}")
    print(f"  ✓ Expires at: {non_stream_container.expires_at}")
else:
    print(f"  ✗ Container is None!")

# ========== Streaming Test ==========
print("\n" + "="*50)
print("Testing Streaming Mode")
print("="*50)

# Streaming request
with client.beta.messages.stream(
    model="claude-sonnet-4-5-20250929",
    betas=["code-execution-2025-08-25"],
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": "Write and run a Python script that prints Hello World"
    }],
    tools=[{
        "type": "code_execution_20250825",
        "name": "code_execution"
    }]
) as stream:
    print("\nStreaming events:")
    stream_container_id = None
    event_counts = {}

    for event in stream:
        event_type = type(event).__name__
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

        # Check for container in message_start event
        if event_type == "BetaRawMessageStartEvent":
            msg = event.message
            if hasattr(msg, 'container') and msg.container:
                stream_container_id = msg.container.id
                print(f"  [message_start] Container ID: {stream_container_id}")
                print(f"  [message_start] Expires at: {msg.container.expires_at}")

    # Print event summary
    print("\n  Event counts:")
    for evt, count in sorted(event_counts.items()):
        print(f"    {evt}: {count}")

    # Get the final message
    final_message = stream.get_final_message()
    print(f"\nFinal message stop_reason: {final_message.stop_reason}")
    print(f"Final message content blocks: {len(final_message.content)}")

    # Print content summary
    for i, block in enumerate(final_message.content):
        block_type = getattr(block, 'type', 'unknown')
        print(f"  Block {i}: {block_type}")

    # Container test result
    print(f"\n[Container Test - Streaming]")
    if stream_container_id:
        print(f"  ✓ Container ID: {stream_container_id}")
    else:
        print(f"  ✗ Container ID not found in message_start!")

print("\n" + "="*50)
print("Streaming Test Complete!")
print("="*50)
