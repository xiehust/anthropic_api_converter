"""
Converter from Anthropic Messages API format to AWS Bedrock Converse API format.

Handles conversion of requests, including messages, tools, system prompts,
and inference parameters.
"""
import base64
import json
from typing import Any, Dict, List, Optional, Union

from app.core.config import settings
from app.schemas.anthropic import (
    ContentBlock,
    ImageContent,
    DocumentContent,
    Message,
    MessageRequest,
    SystemMessage,
    TextContent,
    ThinkingContent,
    Tool,
    ToolResultContent,
    ToolUseContent,
)


class AnthropicToBedrockConverter:
    """Converts Anthropic API format to Bedrock API format."""

    def __init__(self, dynamodb_client=None):
        """Initialize converter with model mapping.

        Args:
            dynamodb_client: Optional DynamoDB client for custom mappings
        """
        self.model_mapping = settings.default_model_mapping
        self.dynamodb_client = dynamodb_client
        self._model_mapping_manager = None
        self._resolved_model_id = None  # Cache the resolved model ID

    def convert_request(self, request: MessageRequest) -> Dict[str, Any]:
        """
        Convert Anthropic MessageRequest to Bedrock Converse request format.

        Args:
            request: Anthropic MessageRequest object

        Returns:
            Dictionary in Bedrock Converse API format
        """
        try:
            print(f"[CONVERTER] Converting Anthropic request to Bedrock format")
            print(f"  - Model: {request.model}")
            print(f"  - Messages: {len(request.messages)}")

            # Convert and cache the model ID
            self._resolved_model_id = self._convert_model_id(request.model)

            bedrock_request = {
                "modelId": self._resolved_model_id,
                "messages": self._convert_messages(request.messages),
                "inferenceConfig": self._convert_inference_config(request),
            }

            print(f"[CONVERTER] Converted model ID: {bedrock_request['modelId']}")
        except Exception as e:
            print(f"\n[ERROR] Exception in convert_request")
            print(f"[ERROR] Type: {type(e).__name__}")
            print(f"[ERROR] Message: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")
            raise

        # Add system messages if present
        if request.system:
            bedrock_request["system"] = self._convert_system(request.system)

        # Add tool configuration if present
        if request.tools:
            bedrock_request["toolConfig"] = self._convert_tool_config(
                request.tools, request.tool_choice
            )

        # Add additional model request fields
        additional_fields = {}
        if request.top_k is not None:
            additional_fields["top_k"] = request.top_k

        # Handle extended thinking if enabled
        if request.thinking and settings.enable_extended_thinking:
            # Map thinking configuration to Bedrock-specific format
            # Note: Bedrock may not have direct thinking support, so we might
            # need to add it to system prompt or handle differently
            thinking_config = self._convert_thinking_config(request.thinking)
            if thinking_config:
                additional_fields.update(thinking_config)

        # Add anthropic_beta features for Claude models
        if self._is_claude_model():
            anthropic_beta = []

            if settings.fine_grained_tool_streaming_enabled:
                anthropic_beta.append("fine-grained-tool-streaming-2025-05-14")

            if settings.interleaved_thinking_enabled:
                anthropic_beta.append("interleaved-thinking-2025-05-14")

            if anthropic_beta:
                additional_fields["anthropic_beta"] = anthropic_beta
                print(f"[CONVERTER] Added anthropic_beta features: {anthropic_beta}")

        if additional_fields:
            bedrock_request["additionalModelRequestFields"] = additional_fields

        return bedrock_request

    def _supports_prompt_caching(self) -> bool:
        """
        Check if the current model supports prompt caching.

        Only Claude models (Anthropic) on Bedrock support prompt caching.

        Returns:
            True if the model supports prompt caching, False otherwise
        """
        if not self._resolved_model_id:
            return False

        model_id_lower = self._resolved_model_id.lower()

        # Check if it's a Claude/Anthropic model
        is_claude = (
            "anthropic" in model_id_lower or
            "claude" in model_id_lower
        )

        if not is_claude and settings.prompt_caching_enabled:
            # Only log once per conversion
            if not hasattr(self, '_logged_cache_skip'):
                print(f"[CONVERTER] Skipping prompt caching for non-Claude model: {self._resolved_model_id}")
                self._logged_cache_skip = True

        return is_claude

    def _is_claude_model(self) -> bool:
        """
        Check if the current model is a Claude model.

        Returns:
            True if the model is Claude/Anthropic, False otherwise
        """
        if not self._resolved_model_id:
            return False

        model_id_lower = self._resolved_model_id.lower()
        return "anthropic" in model_id_lower or "claude" in model_id_lower

    def _convert_model_id(self, anthropic_model_id: str) -> str:
        """
        Convert Anthropic model ID to Bedrock model ARN.

        Resolution priority:
        1. Custom DynamoDB mapping (highest priority)
        2. Default config mapping
        3. Pass-through (use as-is)

        Args:
            anthropic_model_id: Anthropic model identifier

        Returns:
            Bedrock model ARN

        Raises:
            ValueError: If model ID is not found in mapping
        """
        # Priority 1: Check DynamoDB for custom mapping
        if self.dynamodb_client:
            try:
                from app.db.dynamodb import ModelMappingManager
                if not self._model_mapping_manager:
                    self._model_mapping_manager = ModelMappingManager(self.dynamodb_client)

                custom_mapping = self._model_mapping_manager.get_mapping(anthropic_model_id)
                if custom_mapping:
                    print(f"[CONVERTER] Using custom DynamoDB mapping: {anthropic_model_id} → {custom_mapping}")
                    return custom_mapping
            except Exception as e:
                print(f"[CONVERTER] Failed to check DynamoDB mapping: {e}")
                # Continue to default mapping

        # Priority 2: Check default config mapping
        bedrock_model_id = self.model_mapping.get(anthropic_model_id)

        if not bedrock_model_id:
            # Priority 3: Pass-through - assume it's already a valid Bedrock model ID
            # This allows users to directly specify Bedrock model ARNs
            print(f"[CONVERTER] No mapping found, using pass-through: {anthropic_model_id}")
            return anthropic_model_id

        print(f"[CONVERTER] Using default mapping: {anthropic_model_id} → {bedrock_model_id}")
        return bedrock_model_id

    def _convert_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        """
        Convert list of Anthropic messages to Bedrock format.

        Args:
            messages: List of Anthropic Message objects

        Returns:
            List of messages in Bedrock format
        """
        bedrock_messages = []

        for message in messages:
            bedrock_message = {
                "role": message.role,
                "content": self._convert_content_blocks(message.content),
            }
            bedrock_messages.append(bedrock_message)

        return bedrock_messages

    def _convert_content_blocks(
        self, content: Union[str, List[ContentBlock]]
    ) -> List[Dict[str, Any]]:
        """
        Convert Anthropic content blocks to Bedrock format.

        Args:
            content: String or list of ContentBlock objects

        Returns:
            List of content blocks in Bedrock format
        """
        if isinstance(content, str):
            return [{"text": content}]

        bedrock_content = []

        for block in content:
            if isinstance(block, TextContent):
                bedrock_content.append({"text": block.text})
                # Add cache point as a separate block if cache_control is present
                # and model supports prompt caching (Claude models only)
                if block.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                    bedrock_content.append({"cachePoint": {"type": "default"}})
                    print(f"[CONVERTER] Added cachePoint after text block")

            elif isinstance(block, ImageContent):
                # Convert base64 image to bytes
                image_bytes = base64.b64decode(block.source.data)
                # Extract format from media_type (e.g., "image/png" -> "png")
                image_format = block.source.media_type.split("/")[-1]

                bedrock_content.append({
                    "image": {
                        "format": image_format,
                        "source": {"bytes": image_bytes},
                    }
                })
                # Add cache point as a separate block if cache_control is present
                # and model supports prompt caching (Claude models only)
                if block.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                    bedrock_content.append({"cachePoint": {"type": "default"}})
                    print(f"[CONVERTER] Added cachePoint after image block")

            elif isinstance(block, DocumentContent):
                if not settings.enable_document_support:
                    continue

                # Convert base64 document to bytes
                doc_bytes = base64.b64decode(block.source.data)
                doc_format = block.source.media_type.split("/")[-1]

                bedrock_content.append({
                    "document": {
                        "format": doc_format,
                        "name": "document",
                        "source": {"bytes": doc_bytes},
                    }
                })
                # Add cache point as a separate block if cache_control is present
                # and model supports prompt caching (Claude models only)
                if block.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                    bedrock_content.append({"cachePoint": {"type": "default"}})
                    print(f"[CONVERTER] Added cachePoint after document block")

            elif isinstance(block, ThinkingContent):
                # Convert thinking block to text for Bedrock
                # Since Bedrock may not support thinking blocks natively,
                # we can optionally skip them or convert to text
                if settings.enable_extended_thinking:
                    bedrock_content.append(
                        {"text": f"[Thinking: {block.thinking}]"}
                    )

            elif isinstance(block, ToolUseContent):
                bedrock_content.append(
                    {
                        "toolUse": {
                            "toolUseId": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    }
                )

            elif isinstance(block, ToolResultContent):
                # Convert tool result content
                tool_result_content = []
                if isinstance(block.content, str):
                    tool_result_content = [{"text": block.content}]
                else:
                    tool_result_content = self._convert_content_blocks(block.content)

                bedrock_content.append(
                    {
                        "toolResult": {
                            "toolUseId": block.tool_use_id,
                            "content": tool_result_content,
                            "status": "error" if block.is_error else "success",
                        }
                    }
                )

        return bedrock_content

    def _convert_system(
        self, system: Union[str, List[SystemMessage]]
    ) -> List[Dict[str, str]]:
        """
        Convert Anthropic system messages to Bedrock format.

        Args:
            system: String or list of SystemMessage objects

        Returns:
            List of system messages in Bedrock format
        """
        if isinstance(system, str):
            return [{"text": system}]

        bedrock_system = []
        for msg in system:
            bedrock_system.append({"text": msg.text})
            # Add cache point as a separate block if cache_control is present
            # and model supports prompt caching (Claude models only)
            if msg.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                bedrock_system.append({"cachePoint": {"type": "default"}})
                print(f"[CONVERTER] Added cachePoint after system message")

        return bedrock_system

    def _convert_inference_config(
        self, request: MessageRequest
    ) -> Dict[str, Any]:
        """
        Convert inference parameters to Bedrock format.

        Args:
            request: Anthropic MessageRequest object

        Returns:
            Inference config dictionary for Bedrock
        """
        config = {
            "maxTokens": request.max_tokens,
        }

        if request.temperature is not None:
            config["temperature"] = request.temperature

        if request.top_p is not None:
            config["topP"] = request.top_p

        if request.stop_sequences:
            config["stopSequences"] = request.stop_sequences

        return config

    def _convert_tool_config(
        self,
        tools: List[Tool],
        tool_choice: Optional[Union[str, Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Convert Anthropic tool definitions to Bedrock format.

        Args:
            tools: List of Tool objects
            tool_choice: Tool choice specification

        Returns:
            Tool config dictionary for Bedrock
        """
        if not settings.enable_tool_use:
            return {}

        bedrock_tools = []

        for tool in tools:
            bedrock_tool = {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {
                        "json": {
                            "type": tool.input_schema.type,
                            "properties": tool.input_schema.properties,
                        }
                    },
                }
            }

            # Add required fields if present
            if tool.input_schema.required:
                bedrock_tool["toolSpec"]["inputSchema"]["json"][
                    "required"
                ] = tool.input_schema.required

            bedrock_tools.append(bedrock_tool)

            # Add cache point as a separate tool entry if cache_control is present
            # and model supports prompt caching (Claude models only)
            # Note: For tools, cachePoint might need to be at the toolConfig level
            # or added after all tools. This follows the same pattern as content/system.
            if tool.cache_control and settings.prompt_caching_enabled and self._supports_prompt_caching():
                bedrock_tools.append({"cachePoint": {"type": "default"}})
                print(f"[CONVERTER] Added cachePoint after tool: {tool.name}")

        tool_config = {"tools": bedrock_tools}

        # Convert tool choice
        if tool_choice:
            if isinstance(tool_choice, str):
                if tool_choice == "auto":
                    tool_config["toolChoice"] = {"auto": {}}
                elif tool_choice == "any":
                    tool_config["toolChoice"] = {"any": {}}
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
                tool_config["toolChoice"] = {
                    "tool": {"name": tool_choice["name"]}
                }

        return tool_config

    def _convert_thinking_config(
        self, thinking: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Convert Anthropic thinking configuration to Bedrock format.

        Args:
            thinking: Thinking configuration dictionary

        Returns:
            Bedrock-compatible thinking config or None
        """
        # Bedrock may not have direct thinking support
        # This is a placeholder for future implementation
        # For now, we could add thinking instructions to system prompt
        return None

    def get_model_mapping(self, anthropic_model_id: str) -> Optional[str]:
        """
        Get Bedrock model ID for an Anthropic model ID.

        Args:
            anthropic_model_id: Anthropic model identifier

        Returns:
            Bedrock model ARN or None if not found
        """
        return self.model_mapping.get(anthropic_model_id)

    def is_streaming_supported(self, model_id: str) -> bool:
        """
        Check if a model supports streaming.

        Args:
            model_id: Bedrock model ARN

        Returns:
            True if streaming is supported
        """
        # Most Bedrock models support streaming
        # Could be enhanced to check against actual model capabilities
        return True
