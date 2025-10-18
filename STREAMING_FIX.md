# Streaming Support for Thinking Models

## Problem

The streaming functionality was failing with `IndexError: list index out of range` when using thinking models like `openai.gpt-oss-120b-1:0`.

### Root Cause

1. **Thinking models** output `reasoningContent` (internal thinking/reasoning) before the final text response
2. **Bedrock doesn't send `contentBlockStart` events** - it jumps straight to `contentBlockDelta` events
3. **Multiple content blocks** are returned:
   - Index 0: Empty initial block
   - Index 1: Reasoning content (`reasoningContent`)
   - Index 2: Actual text response
4. The Anthropic SDK expects **`content_block_start` before any `content_block_delta`** events
5. Bedrock's `reasoningContent` is a dict with `{"text": "..."}` structure

## Solution Implemented

### Changes to `app/services/bedrock_service.py` (lines 116-144)

1. **Track seen content block indices** using a `seen_indices` set
2. **Inject `content_block_start` events** when we see a new content block index
3. **Detect thinking content** and emit proper `type: "thinking"` content_block_start events
4. **Send thinking deltas** to clients as proper Anthropic thinking blocks

```python
seen_indices = set()  # Track which content block indices we've seen

for bedrock_event in stream:
    if "contentBlockDelta" in bedrock_event:
        delta_data = bedrock_event["contentBlockDelta"]
        index = delta_data.get("contentBlockIndex", 0)
        delta = delta_data.get("delta", {})

        # If we haven't seen this index yet, inject a content_block_start event
        if index not in seen_indices:
            seen_indices.add(index)

            # Check if this is reasoning content (thinking models)
            if "reasoningContent" in delta:
                # Inject a content_block_start event for thinking content
                start_event = {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "thinking", "thinking": ""},
                }
                yield self._format_sse_event(start_event)
            else:
                # Inject a content_block_start event for regular text
                start_event = {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                }
                yield self._format_sse_event(start_event)
```

### Changes to `app/converters/bedrock_to_anthropic.py` (lines 249-266)

Convert `reasoningContent` to Anthropic `thinking_delta` format:

```python
# Handle reasoning content (thinking models output)
if "reasoningContent" in delta:
    # Extract text from reasoningContent (it's a dict with "text" key)
    reasoning_text = delta["reasoningContent"]
    if isinstance(reasoning_text, dict):
        reasoning_text = reasoning_text.get("text", "")

    # Convert Bedrock reasoningContent to Anthropic thinking_delta
    events.append(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {
                "type": "thinking_delta",
                "thinking": reasoning_text,
            },
        }
    )
elif "text" in delta:
    # Process regular text
```

## Event Flow After Fix

For a thinking model request, the event flow is now:

1. `message_start` - Message begins
2. `content_block_start` (index 0) - Empty block (injected)
3. `content_block_delta` (index 0) - Empty text
4. `content_block_stop` (index 0)
5. `content_block_start` (index 1, type: "thinking") - **Injected automatically**
6. `content_block_delta` (index 1, type: "thinking_delta") - Reasoning/thinking content
7. `content_block_stop` (index 1)
8. `content_block_start` (index 2, type: "text") - **Injected automatically**
9. `content_block_delta` (index 2, type: "text_delta") - Actual response text
10. `content_block_stop` (index 2)
11. `message_delta` - Stop reason and usage
12. `message_stop` - Message complete

## Testing

To test the fix, start the server on port 8000:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Then run the test:

```bash
uv run python tests/quick_test.py
```

Expected output: **4/4 tests passed** ✓

## Notes

- **Reasoning content** from thinking models is now properly exposed as `thinking_delta` events
- The Anthropic SDK supports thinking blocks via `ThinkingContent`, `ThinkingDelta`, and related types
- The fix is backward compatible with non-thinking models
- Regular Claude models that send proper `contentBlockStart` events continue to work as before
- Bedrock's `reasoningContent` structure (`{"text": "..."}`) is properly converted to a plain string

## Related Files

- `app/services/bedrock_service.py` - Main streaming logic
- `app/converters/bedrock_to_anthropic.py` - Event conversion
- `tests/quick_test.py` - Test script
