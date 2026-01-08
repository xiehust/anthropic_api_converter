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
print("Testing None-Streaming Mode")
print("="*50)
# Extract the container ID from the first response
print(response1.content)

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
    for event in stream:
        event_type = type(event).__name__
        print(f"  {event_type}: {event}")

    # Get the final message
    final_message = stream.get_final_message()
    print(f"\nFinal message stop_reason: {final_message.stop_reason}")
    print(f"Final message content blocks: {len(final_message.content)}")

    # Print content summary
    for i, block in enumerate(final_message.content):
        block_type = getattr(block, 'type', 'unknown')
        print(f"  Block {i}: {block_type}")

print("\n" + "="*50)
print("Streaming Test Complete!")
print("="*50)
