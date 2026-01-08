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
        "content": "Write a file with a number 3 and save it to '/tmp/number.txt'"
    }],
    tools=[{
        "type": "code_execution_20250825",
        "name": "code_execution"
    }]
)

# Extract the container ID from the first response
container_id = response1.container.id
print(f"container id:{container_id}")
print(response1.content[-1])
# Second request: Reuse the container to read the file
response2 = client.beta.messages.create(
    container=container_id,  # Reuse the same container
    model="claude-sonnet-4-5-20250929",
    betas=["code-execution-2025-08-25"],
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": "Read the number from '/tmp/number.txt' and calculate its square"
    }],
    tools=[{
        "type": "code_execution_20250825",
        "name": "code_execution"
    }]
)
container_id2 = response2.container.id
print(f"container id:{container_id2}")
print(response2.content[-1])

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
        "content": "List files in /tmp directory"
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
