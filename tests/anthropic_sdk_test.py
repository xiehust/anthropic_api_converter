import argparse
from anthropic import Anthropic

# Import test configuration
from config import API_KEY, BASE_URL

# Initialize client
client = Anthropic(api_key=API_KEY, base_url=BASE_URL)

text_long = ""
with open("daming.txt", 'r', encoding='gbk') as f:
    text_long = f.read()


def print_stream_events(stream):
    """Print streaming events and return the final message."""
    for event in stream:
        if event.type == "content_block_start":
            print(f"\n[content_block_start] type={event.content_block.type}")
        elif event.type == "content_block_delta":
            if event.delta.type == "text_delta":
                print(event.delta.text, end="", flush=True)
            elif event.delta.type == "thinking_delta":
                print(f"[thinking] {event.delta.thinking}", end="", flush=True)
        elif event.type == "content_block_stop":
            print(f"\n[content_block_stop]")
        elif event.type == "message_start":
            print(f"[message_start] model={event.message.model}")
        elif event.type == "message_delta":
            print(f"\n[message_delta] stop_reason={event.delta.stop_reason} usage={event.usage}")
        elif event.type == "message_stop":
            print("[message_stop]")
    return stream.get_final_message()


def test_non_stream():
    print("=" * 60)
    print("NON-STREAMING MODE TEST")
    print("=" * 60)

    messages = [{"role": "user", "content": f"{text_long[:100000]}"}]

    response = client.beta.messages.create(
        model="claude-opus-4-6",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low"
        },
        betas=["compact-2026-01-12"],
        context_management={
            "edits": [
                {
                    "type": "compact_20260112",
                    "trigger": {
                        "type": "input_tokens",
                        "value": 50000
                    },
                    "pause_after_compaction": True
                }
            ]
        },
        messages=messages
    )

    # Check if compaction triggered a pause
    if response.stop_reason == "compaction":
        print(response)
        messages.append({"role": "assistant", "content": response.content})

        # Continue the request
        response = client.beta.messages.create(
            betas=["compact-2026-01-12"],
            model="claude-opus-4-6",
            max_tokens=16000,
            messages=messages,
            context_management={
                "edits": [
                    {
                        "type": "compact_20260112",
                        "trigger": {
                            "type": "input_tokens",
                            "value": 50000
                        }
                    }
                ]
            }
        )

    print(response)


def test_stream():
    print("=" * 60)
    print("STREAMING MODE TEST")
    print("=" * 60)

    messages = [{"role": "user", "content": f"{text_long[:100000]}"}]

    with client.beta.messages.stream(
        model="claude-opus-4-6",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low"
        },
        betas=["compact-2026-01-12"],
        context_management={
            "edits": [
                {
                    "type": "compact_20260112",
                    "trigger": {
                        "type": "input_tokens",
                        "value": 50000
                    },
                    "pause_after_compaction": True
                }
            ]
        },
        messages=messages
    ) as stream:
        final_message = print_stream_events(stream)
        print(f"\nFirst stream usage: input={final_message.usage.input_tokens}, output={final_message.usage.output_tokens}")

    # Check if compaction triggered a pause
    if final_message.stop_reason == "compaction":
        print("\n[compaction detected] Continuing with compacted context...")
        messages.append({"role": "assistant", "content": final_message.content})

        with client.beta.messages.stream(
            model="claude-opus-4-6",
            max_tokens=16000,
            betas=["compact-2026-01-12"],
            messages=messages,
            context_management={
                "edits": [
                    {
                        "type": "compact_20260112",
                        "trigger": {
                            "type": "input_tokens",
                            "value": 50000
                        }
                    }
                ]
            }
        ) as stream:
            final_message = print_stream_events(stream)
            print(f"\nContinuation usage: input={final_message.usage.input_tokens}, output={final_message.usage.output_tokens}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anthropic SDK test")
    parser.add_argument("--stream", action="store_true", help="Run streaming mode test")
    parser.add_argument("--no-stream", action="store_true", help="Run non-streaming mode test")
    args = parser.parse_args()

    # Default: run both if neither flag specified
    run_non_stream = args.no_stream or (not args.stream and not args.no_stream)
    run_stream = args.stream or (not args.stream and not args.no_stream)

    print(f"Input text preview: {text_long[:100]}")
    print()

    if run_non_stream:
        test_non_stream()

    if run_stream:
        if run_non_stream:
            print("\n")
        test_stream()
