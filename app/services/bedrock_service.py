"""
Bedrock service for interacting with AWS Bedrock Converse API.

Handles both streaming and non-streaming requests to Bedrock models.
"""
import json
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
from app.converters.bedrock_to_anthropic import BedrockToAnthropicConverter
from app.core.config import settings
from app.schemas.anthropic import CountTokensRequest, MessageRequest, MessageResponse


class BedrockService:
    """Service for interacting with AWS Bedrock."""

    def __init__(self, dynamodb_client=None):
        """Initialize Bedrock service.

        Args:
            dynamodb_client: Optional DynamoDB client for custom model mappings
        """
        # Configure boto3 with timeout settings
        config = Config(
            read_timeout=settings.bedrock_timeout,
            connect_timeout=30,
            retries={"max_attempts": 3, "mode": "adaptive"},
        )

        self.client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            endpoint_url=settings.bedrock_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_session_token=settings.aws_session_token,
            config=config,
        )

        # Initialize DynamoDB client if not provided
        if dynamodb_client is None:
            from app.db.dynamodb import DynamoDBClient
            dynamodb_client = DynamoDBClient()

        self.dynamodb_client = dynamodb_client
        self.anthropic_to_bedrock = AnthropicToBedrockConverter(dynamodb_client)
        self.bedrock_to_anthropic = BedrockToAnthropicConverter()

    def invoke_model(
        self, request: MessageRequest, request_id: Optional[str] = None
    ) -> MessageResponse:
        """
        Invoke Bedrock model (non-streaming).

        Args:
            request: Anthropic MessageRequest
            request_id: Optional request ID

        Returns:
            Anthropic MessageResponse

        Raises:
            Exception: If Bedrock API call fails
        """
        print(f"[BEDROCK] Converting request to Bedrock format for request {request_id}")

        # Convert request to Bedrock format
        bedrock_request = self.anthropic_to_bedrock.convert_request(request)

        print(f"[BEDROCK] Bedrock request params:")
        print(f"  - Model ID: {bedrock_request.get('modelId')}")
        print(f"  - Messages count: {len(bedrock_request.get('messages', []))}")
        print(f"  - Has system: {bool(bedrock_request.get('system'))}")
        print(f"  - Has tools: {bool(bedrock_request.get('toolConfig'))}")

        try:
            print(f"[BEDROCK] Calling Bedrock Converse API...")

            # Call Bedrock Converse API
            response = self.client.converse(**bedrock_request)

            print(f"[BEDROCK] Received response from Bedrock")
            print(f"  - Stop reason: {response.get('stopReason')}")
            print(f"  - Usage: {response.get('usage')}")

            # Convert response back to Anthropic format
            message_id = request_id or f"msg_{uuid4().hex}"
            anthropic_response = self.bedrock_to_anthropic.convert_response(
                response, request.model, message_id
            )

            print(f"[BEDROCK] Successfully converted response to Anthropic format")

            return anthropic_response

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            print(f"\n[ERROR] Bedrock ClientError in request {request_id}")
            print(f"[ERROR] Code: {error_code}")
            print(f"[ERROR] Message: {error_message}")
            print(f"[ERROR] Response: {e.response}\n")
            raise Exception(f"Bedrock API error [{error_code}]: {error_message}")
        except Exception as e:
            print(f"\n[ERROR] Exception in Bedrock invoke_model for request {request_id}")
            print(f"[ERROR] Type: {type(e).__name__}")
            print(f"[ERROR] Message: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")
            raise Exception(f"Failed to invoke Bedrock model: {str(e)}")

    async def invoke_model_stream(
        self, request: MessageRequest, request_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        Invoke Bedrock model with streaming (Server-Sent Events format).

        Args:
            request: Anthropic MessageRequest
            request_id: Optional request ID

        Yields:
            SSE-formatted event strings

        Raises:
            Exception: If Bedrock API call fails
        """
        print(f"[BEDROCK STREAM] Converting request to Bedrock format for request {request_id}")

        # Convert request to Bedrock format
        bedrock_request = self.anthropic_to_bedrock.convert_request(request)

        print(f"[BEDROCK STREAM] Bedrock request params:")
        print(f"  - Model ID: {bedrock_request.get('modelId')}")
        print(f"  - Messages count: {len(bedrock_request.get('messages', []))}")

        message_id = request_id or f"msg_{uuid4().hex}"
        current_index = 0
        accumulated_usage = {"inputTokens": 0, "outputTokens": 0}
        seen_indices = set()  # Track which content block indices we've seen

        try:
            print(f"[BEDROCK STREAM] Calling Bedrock ConverseStream API...")

            # Call Bedrock ConverseStream API
            response = self.client.converse_stream(**bedrock_request)

            # Process stream events
            stream = response.get("stream")
            if not stream:
                print(f"[ERROR] No stream returned from Bedrock for request {request_id}")
                raise Exception("No stream returned from Bedrock")

            print(f"[BEDROCK STREAM] Starting to process stream events...")

            for bedrock_event in stream:
                # Handle missing contentBlockStart events from Bedrock
                # Some models (like thinking models) don't send contentBlockStart
                if "contentBlockDelta" in bedrock_event:
                    delta_data = bedrock_event["contentBlockDelta"]
                    index = delta_data.get("contentBlockIndex", 0)
                    delta = delta_data.get("delta", {})

                    # If we haven't seen this index yet, inject a content_block_start event
                    if index not in seen_indices:
                        seen_indices.add(index)

                        # Check if this is reasoning content (thinking models)
                        if "reasoningContent" in delta:
                            print(f"[BEDROCK STREAM] Injecting content_block_start for thinking block [{index}] (Bedrock didn't send it)")
                            # Inject a content_block_start event for thinking content
                            start_event = {
                                "type": "content_block_start",
                                "index": index,
                                "content_block": {"type": "thinking", "thinking": ""},
                            }
                            yield self._format_sse_event(start_event)
                        else:
                            print(f"[BEDROCK STREAM] Injecting content_block_start for text block [{index}] (Bedrock didn't send it)")
                            # Inject a content_block_start event for regular text
                            start_event = {
                                "type": "content_block_start",
                                "index": index,
                                "content_block": {"type": "text", "text": ""},
                            }
                            yield self._format_sse_event(start_event)

                # Convert Bedrock event to Anthropic events
                anthropic_events = self.bedrock_to_anthropic.convert_stream_event(
                    bedrock_event, request.model, message_id, current_index
                )

                # Update current index if we see content block events
                if "contentBlockStart" in bedrock_event:
                    current_index = bedrock_event["contentBlockStart"].get(
                        "contentBlockIndex", current_index
                    )
                    seen_indices.add(current_index)

                # Update accumulated usage from metadata
                if "metadata" in bedrock_event:
                    metadata = bedrock_event["metadata"]
                    usage = metadata.get("usage", {})
                    accumulated_usage["inputTokens"] = usage.get("inputTokens", 0)
                    accumulated_usage["outputTokens"] = usage.get("outputTokens", 0)

                    # Merge usage into events
                    anthropic_events = (
                        self.bedrock_to_anthropic.merge_usage_into_events(
                            anthropic_events, usage
                        )
                    )

                # Yield each Anthropic event as SSE
                for event in anthropic_events:
                    yield self._format_sse_event(event)

            print(f"[BEDROCK STREAM] Stream completed for request {request_id}")
            print(f"  - Final usage: {accumulated_usage}")

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]

            print(f"\n[ERROR] Bedrock ClientError in streaming request {request_id}")
            print(f"[ERROR] Code: {error_code}")
            print(f"[ERROR] Message: {error_message}")
            print(f"[ERROR] Response: {e.response}\n")

            # Send error event
            error_event = self.bedrock_to_anthropic.create_error_event(
                error_code, error_message
            )
            yield self._format_sse_event(error_event)

        except Exception as e:
            print(f"\n[ERROR] Exception in Bedrock streaming for request {request_id}")
            print(f"[ERROR] Type: {type(e).__name__}")
            print(f"[ERROR] Message: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")

            # Send error event
            error_event = self.bedrock_to_anthropic.create_error_event(
                "internal_error", str(e)
            )
            yield self._format_sse_event(error_event)

    def _format_sse_event(self, event: Dict[str, Any]) -> str:
        """
        Format event as Server-Sent Event.

        Args:
            event: Event dictionary

        Returns:
            SSE-formatted string
        """
        # Anthropic SSE format:
        # event: {event_type}
        # data: {json_data}
        # (blank line)

        event_type = event.get("type", "unknown")
        event_data = json.dumps(event)

        return f"event: {event_type}\ndata: {event_data}\n\n"

    def list_available_models(self) -> list[Dict[str, Any]]:
        """
        List available Bedrock models.

        Returns:
            List of model information dictionaries
        """
        try:
            bedrock_client = boto3.client(
                "bedrock",
                region_name=settings.aws_region,
                endpoint_url=settings.bedrock_endpoint_url,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                aws_session_token=settings.aws_session_token,
            )

            response = bedrock_client.list_foundation_models()
            models = response.get("modelSummaries", [])

            # Filter to only models that support converse API
            converse_models = []
            for model in models:
                # Check if model supports text generation
                if "TEXT" in model.get("outputModalities", []):
                    converse_models.append(
                        {
                            "id": model.get("modelId"),
                            "name": model.get("modelName"),
                            "provider": model.get("providerName"),
                            "input_modalities": model.get("inputModalities", []),
                            "output_modalities": model.get("outputModalities", []),
                            "streaming_supported": model.get(
                                "responseStreamingSupported", False
                            ),
                        }
                    )

            return converse_models

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            raise Exception(f"Failed to list models [{error_code}]: {error_message}")
        except Exception as e:
            raise Exception(f"Failed to list models: {str(e)}")

    def get_model_info(self, model_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific model.

        Args:
            model_id: Bedrock model ID

        Returns:
            Model information or None if not found
        """
        try:
            bedrock_client = boto3.client(
                "bedrock",
                region_name=settings.aws_region,
                endpoint_url=settings.bedrock_endpoint_url,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                aws_session_token=settings.aws_session_token,
            )

            response = bedrock_client.get_foundation_model(modelIdentifier=model_id)
            model_details = response.get("modelDetails", {})

            return {
                "id": model_details.get("modelId"),
                "name": model_details.get("modelName"),
                "provider": model_details.get("providerName"),
                "input_modalities": model_details.get("inputModalities", []),
                "output_modalities": model_details.get("outputModalities", []),
                "streaming_supported": model_details.get(
                    "responseStreamingSupported", False
                ),
                "customizations_supported": model_details.get(
                    "customizationsSupported", []
                ),
            }

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise Exception(f"Failed to get model info: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to get model info: {str(e)}")

    def count_tokens(self, request: CountTokensRequest) -> int:
        """
        Count tokens in a request.

        This method first checks if the model is an Anthropic/Claude model.
        For Claude models, it uses Bedrock's Converse API to get actual token counts.
        For other models or if the API fails, it falls back to estimation.

        Args:
            request: CountTokensRequest with model, messages, system, and tools

        Returns:
            Input token count (actual or estimated)

        Note:
            For Claude models on Bedrock, this returns actual token counts.
            For other models, this returns an estimation.
        """
        # Check if this is an Anthropic/Claude model
        model_id = request.model.lower()
        is_claude_model = (
            "anthropic" in model_id or
            "claude" in model_id
        )

        # Only try Bedrock API for Claude models
        if is_claude_model:
            try:
                # Convert the request to MessageRequest format for conversion
                message_request = MessageRequest(
                    model=request.model,
                    messages=request.messages,
                    system=request.system,
                    tools=request.tools,
                    max_tokens=1,  # Required but not used for counting
                )

                # Convert to Bedrock format
                bedrock_request = self.anthropic_to_bedrock.convert_request(message_request)

                # Build count_tokens API request
                count_tokens_input = {
                    "converse": {
                        "messages": bedrock_request["messages"]
                    }
                }

                # Add system messages if present
                if "system" in bedrock_request and bedrock_request["system"]:
                    count_tokens_input["converse"]["system"] = bedrock_request["system"]

                # Add tool config if present
                if "toolConfig" in bedrock_request:
                    count_tokens_input["converse"]["toolConfig"] = bedrock_request["toolConfig"]

                # Call count_tokens API
                response = self.client.count_tokens(
                    modelId=bedrock_request["modelId"],
                    input=count_tokens_input
                )

                # Extract token count
                input_tokens = response.get("inputTokens", 0)

                if input_tokens > 0:
                    return input_tokens

            except Exception as e:
                # If Bedrock API fails, fall back to estimation
                # This can happen for API errors or permission issues
                pass

        # Fallback: Estimate token count for non-Claude models or if API fails
        return self._estimate_token_count(request)

    def _estimate_token_count(self, request: CountTokensRequest) -> int:
        """
        Estimate token count using heuristics.

        This method estimates tokens based on character count with adjustments
        for Chinese/Japanese/Korean characters.

        Args:
            request: CountTokensRequest with model, messages, system, and tools

        Returns:
            Estimated input token count
        """
        # Convert the request to a MessageRequest format for conversion
        message_request = MessageRequest(
            model=request.model,
            messages=request.messages,
            system=request.system,
            tools=request.tools,
            max_tokens=1,  # Required but not used for counting
        )

        # Convert to Bedrock format to get the full formatted request
        bedrock_request = self.anthropic_to_bedrock.convert_request(message_request)

        # Collect all text content for analysis
        all_text = []

        # Collect system message text
        if "system" in bedrock_request:
            for system_msg in bedrock_request["system"]:
                if "text" in system_msg:
                    all_text.append(system_msg["text"])

        # Collect message text
        for message in bedrock_request.get("messages", []):
            for content in message.get("content", []):
                if "text" in content:
                    all_text.append(content["text"])

        # Collect tool definition text
        if "toolConfig" in bedrock_request:
            tools = bedrock_request["toolConfig"].get("tools", [])
            for tool in tools:
                if "toolSpec" in tool:
                    spec = tool["toolSpec"]
                    all_text.append(spec.get("name", ""))
                    all_text.append(spec.get("description", ""))
                    if "inputSchema" in spec:
                        all_text.append(json.dumps(spec["inputSchema"]))

        # Count tokens based on content
        total_tokens = 0

        for text in all_text:
            if text:
                # Detect if text contains CJK (Chinese, Japanese, Korean) characters
                cjk_chars = sum(1 for char in text if self._is_cjk_char(char))
                non_cjk_chars = len(text) - cjk_chars

                # CJK characters: approximately 1 token per character
                # English/Western characters: approximately 1 token per 4 characters
                total_tokens += cjk_chars
                total_tokens += non_cjk_chars // 4

        # Count images and documents
        for message in bedrock_request.get("messages", []):
            for content in message.get("content", []):
                if "image" in content:
                    # Images typically count as ~85 tokens per image for Claude
                    total_tokens += 85
                elif "document" in content:
                    # Documents vary, estimate ~250 tokens
                    total_tokens += 250

        # Add overhead for formatting and special tokens (~5% overhead)
        total_tokens = int(total_tokens * 1.05)

        # Minimum 1 token
        return max(1, total_tokens)

    @staticmethod
    def _is_cjk_char(char: str) -> bool:
        """
        Check if a character is CJK (Chinese, Japanese, Korean).

        Args:
            char: Single character to check

        Returns:
            True if character is CJK, False otherwise
        """
        # Unicode ranges for CJK characters
        cjk_ranges = [
            (0x4E00, 0x9FFF),    # CJK Unified Ideographs
            (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
            (0x20000, 0x2A6DF),  # CJK Unified Ideographs Extension B
            (0x2A700, 0x2B73F),  # CJK Unified Ideographs Extension C
            (0x2B740, 0x2B81F),  # CJK Unified Ideographs Extension D
            (0x2B820, 0x2CEAF),  # CJK Unified Ideographs Extension E
            (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
            (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
            (0x3040, 0x309F),    # Hiragana
            (0x30A0, 0x30FF),    # Katakana
            (0xAC00, 0xD7AF),    # Hangul Syllables
        ]

        code_point = ord(char)
        return any(start <= code_point <= end for start, end in cjk_ranges)
