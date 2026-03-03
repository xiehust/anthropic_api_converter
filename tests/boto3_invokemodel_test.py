import argparse
import json
import boto3

# Bedrock model ID for Claude
MODEL_ID = "global.anthropic.claude-opus-4-6-v1"
ANTHROPIC_VERSION = "bedrock-2023-05-31"
AWS_REGION = "us-east-1"

# Read long text file
text_long = "思考如何突破广义相对论，实现超光速"
text_long = """
Problem 1. A line in the plane is called sunny if it is not parallel to any of the x-axis, the y-axis,
and the line x + y = 0.
Let n ⩾ 3 be a given integer. Determine all nonnegative integers k such that there exist n distinct
lines in the plane satisfying both of the following:
• for all positive integers a and b with a + b ⩽ n + 1, the point (a, b) is on at least one of the
lines; and
• exactly k of the n lines are sunny
"""
# with open("daming.txt", "r", encoding="gbk") as f:
#     text_long = f.read()

# Initialize Bedrock Runtime client
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def print_stream_events(response_stream):
    """Print streaming events from Bedrock invoke_model_with_response_stream and return parsed result."""
    full_text = ""
    thinking_text = ""
    input_tokens = 0
    output_tokens = 0
    stop_reason = None

    for event in response_stream["body"]:
        chunk = json.loads(event["chunk"]["bytes"])
        event_type = chunk.get("type")

        if event_type == "message_start":
            msg = chunk.get("message", {})
            print(f"[message_start] model={msg.get('model')}")
            usage = msg.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)

        elif event_type == "content_block_start":
            cb = chunk.get("content_block", {})
            print(f"\n[content_block_start] type={cb.get('type')}")

        elif event_type == "content_block_delta":
            delta = chunk.get("delta", {})
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                text = delta.get("text", "")
                print(text, end="", flush=True)
                full_text += text
            elif delta_type == "thinking_delta":
                thinking = delta.get("thinking", "")
                print(f"{thinking}", end="", flush=True)
                thinking_text += thinking

        elif event_type == "content_block_stop":
            print(f"\n[content_block_stop]")

        elif event_type == "message_delta":
            delta = chunk.get("delta", {})
            usage = chunk.get("usage", {})
            stop_reason = delta.get("stop_reason")
            output_tokens = usage.get("output_tokens", 0)
            print(f"\n[message_delta] stop_reason={stop_reason} output_tokens={output_tokens}")

        elif event_type == "message_stop":
            print("[message_stop]")

    return {
        "text": full_text,
        "thinking": thinking_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "stop_reason": stop_reason,
    }


def build_request_body(messages, **kwargs):
    """Build Anthropic-native request body for Bedrock invoke_model."""
    body = {
        "anthropic_version": ANTHROPIC_VERSION,
        "messages": messages,
        "max_tokens": kwargs.get("max_tokens", 16000),
    }
    if "thinking" in kwargs:
        body["thinking"] = kwargs["thinking"]
    if "output_config" in kwargs:
        body["output_config"] = kwargs["output_config"]
    return body


def test_non_stream():
    print("=" * 60)
    print("NON-STREAMING MODE TEST (boto3 invoke_model)")
    print("=" * 60)

    messages = [{"role": "user", "content": text_long[:100000]}]

    body = build_request_body(
        messages,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
    )

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )

    result = json.loads(response["body"].read())
    print(json.dumps(result, indent=2, ensure_ascii=False))


def test_stream():
    print("=" * 60)
    print("STREAMING MODE TEST (boto3 invoke_model_with_response_stream)")
    print("=" * 60)

    messages = [{"role": "user", "content": text_long[:100000]}]

    body = build_request_body(
        messages,
        max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
    )

    response = bedrock.invoke_model_with_response_stream(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )

    result = print_stream_events(response)
    print(f"\nStream usage: input={result['input_tokens']}, output={result['output_tokens']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Boto3 Bedrock invoke_model test")
    parser.add_argument("--stream", action="store_true", help="Run streaming mode test")
    parser.add_argument("--no-stream", action="store_true", help="Run non-streaming mode test")
    parser.add_argument("--model", type=str, default=MODEL_ID, help="Bedrock model ID")
    parser.add_argument("--region", type=str, default=AWS_REGION, help="AWS region")
    args = parser.parse_args()

    if args.model != MODEL_ID:
        MODEL_ID = args.model
    if args.region != AWS_REGION:
        bedrock = boto3.client("bedrock-runtime", region_name=args.region)

    # Default: run both if neither flag specified
    run_non_stream = args.no_stream or (not args.stream and not args.no_stream)
    run_stream = args.stream or (not args.stream and not args.no_stream)

    print(f"Model: {MODEL_ID}")
    print(f"Region: {args.region}")
    print(f"Input text preview: {text_long[:100]}")
    print()

    if run_non_stream:
        test_non_stream()

    if run_stream:
        if run_non_stream:
            print("\n")
        test_stream()
