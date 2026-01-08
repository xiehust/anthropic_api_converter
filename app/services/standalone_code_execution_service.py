"""
Standalone Code Execution Service.

Handles server-side code execution for the code-execution-2025-08-25 beta feature.
Unlike PTC (which returns tool calls to the client for execution), this service
executes bash commands and file operations entirely server-side.

Flow:
1. Detect standalone code execution requests (beta header + tool config)
2. Call Claude/Bedrock
3. If response contains server_tool_use (bash/text_editor) → execute in sandbox
4. Build server_tool_result and call Claude again
5. Repeat until stop_reason != "tool_use" or max iterations
6. Return full trace (all content blocks) to client
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.core.config import settings
from app.schemas.anthropic import (
    MessageRequest,
    MessageResponse,
    Usage,
)
from app.schemas.ptc import (
    STANDALONE_CODE_EXEC_BETA_HEADER,
    STANDALONE_TOOL_TYPE,
    PTC_ALLOWED_CALLER,
    ContainerInfo,
)
from app.services.ptc import (
    StandaloneSandboxExecutor,
    StandaloneSandboxConfig,
    StandaloneSandboxSession,
)

logger = logging.getLogger(__name__)

# Maximum agentic loop iterations
MAX_ITERATIONS = 25


class StandaloneCodeExecutionService:
    """
    Service for handling standalone code execution requests.

    Runs an agentic loop where Claude can call bash_code_execution and
    text_editor_code_execution tools, which are executed server-side.
    """

    def __init__(self):
        self._sandbox_executor: Optional[StandaloneSandboxExecutor] = None

    @property
    def sandbox_executor(self) -> StandaloneSandboxExecutor:
        """Lazy-load sandbox executor."""
        if self._sandbox_executor is None:
            config = StandaloneSandboxConfig(
                image=settings.ptc_sandbox_image,
                memory_limit=settings.ptc_memory_limit,
                timeout_seconds=settings.ptc_execution_timeout,
                network_disabled=settings.ptc_network_disabled,
                session_timeout_seconds=settings.ptc_session_timeout,
                bash_timeout_seconds=getattr(settings, 'standalone_bash_timeout', 30),
            )
            self._sandbox_executor = StandaloneSandboxExecutor(config)
            self._sandbox_executor.start_cleanup_task()
        return self._sandbox_executor

    @staticmethod
    def is_standalone_request(request: MessageRequest, beta_header: Optional[str]) -> bool:
        """
        Check if request is a standalone code execution request.

        Conditions (all must be true):
        1. Feature is enabled in settings
        2. Beta header contains 'code-execution-2025-08-25'
        3. Tools include code_execution_20250825 type
        4. NO tools have allowed_callers (distinguishes from PTC)

        Args:
            request: The message request
            beta_header: The anthropic-beta header value

        Returns:
            True if this is a standalone code execution request
        """
        # Check if feature is enabled
        if not getattr(settings, 'enable_standalone_code_execution', True):
            return False

        # Check beta header
        if not beta_header or STANDALONE_CODE_EXEC_BETA_HEADER not in beta_header:
            return False

        # Check for code_execution tool
        if not request.tools:
            return False

        has_code_execution_tool = False
        has_allowed_callers = False

        for tool in request.tools:
            tool_dict = tool if isinstance(tool, dict) else tool.model_dump()

            # Check for code_execution_20250825 tool type
            if tool_dict.get("type") == STANDALONE_TOOL_TYPE:
                has_code_execution_tool = True

            # Check if any tool has allowed_callers (indicates PTC, not standalone)
            allowed_callers = tool_dict.get("allowed_callers")
            if allowed_callers and PTC_ALLOWED_CALLER in allowed_callers:
                has_allowed_callers = True

        # Standalone requires code_execution tool but NO allowed_callers
        # (If there are allowed_callers, it's PTC, not standalone)
        return has_code_execution_tool and not has_allowed_callers

    def is_docker_available(self) -> bool:
        """Check if Docker is available for code execution."""
        try:
            return self.sandbox_executor.is_docker_available()
        except Exception:
            return False

    def _get_standalone_tools(self) -> List[Dict[str, Any]]:
        """
        Get the tool definitions for standalone code execution.

        Currently only bash_code_execution is supported.
        text_editor_code_execution is disabled (requires Files API).

        Returns:
            List of tool definitions in Anthropic format
        """
        return [
            {
                "name": "bash_code_execution",
                "description": "Execute a bash command in the sandbox environment. "
                    "Use this to run shell commands, scripts, or system operations. "
                    "The command will be executed in a secure Docker container. "
                    "You can use standard bash commands like cat, echo, etc. for file operations.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The bash command to execute"
                        },
                        "restart": {
                            "type": "boolean",
                            "description": "Whether to restart the shell before executing (default: false)"
                        }
                    },
                    "required": ["command"]
                }
            },
            # text_editor_code_execution is disabled - requires Files API
        ]

    def _build_tools_for_request(self, original_tools: Optional[List[Any]]) -> List[Any]:
        """
        Build the tool list for Bedrock request.

        Replaces the code_execution_20250825 marker with actual tool definitions.

        Args:
            original_tools: Original tools from request

        Returns:
            Tools list with standalone tools injected
        """
        if not original_tools:
            return self._get_standalone_tools()

        # Filter out the code_execution marker and keep other tools
        result: List[Any] = []
        has_code_execution = False

        for tool in original_tools:
            if isinstance(tool, dict):
                if tool.get("type") == STANDALONE_TOOL_TYPE:
                    has_code_execution = True
                    continue  # Skip the marker
            elif hasattr(tool, "type") and tool.type == STANDALONE_TOOL_TYPE:
                has_code_execution = True
                continue
            result.append(tool)

        # Add standalone tools if code_execution was present
        if has_code_execution:
            result.extend(self._get_standalone_tools())

        return result

    def _filter_beta_header(self, anthropic_beta: Optional[str]) -> Optional[str]:
        """
        Filter out the standalone code execution beta header.

        The code-execution-2025-08-25 header is a local feature that Bedrock
        doesn't recognize. We filter it out before calling Bedrock.

        Args:
            anthropic_beta: Comma-separated beta header values

        Returns:
            Filtered beta header string, or None if all headers were filtered
        """
        if not anthropic_beta:
            return None

        # Split and filter
        headers = [h.strip() for h in anthropic_beta.split(",")]
        filtered = [h for h in headers if h != STANDALONE_CODE_EXEC_BETA_HEADER]

        if not filtered:
            return None
        return ",".join(filtered)

    async def handle_request(
        self,
        request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        container_id: Optional[str] = None,
        anthropic_beta: Optional[str] = None,
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Handle standalone code execution request.

        This runs an agentic loop:
        1. Call Bedrock/Claude
        2. If response contains server_tool_use → execute in sandbox
        3. Build server_tool_result and call Claude again
        4. Repeat until stop_reason != "tool_use" or max iterations
        5. Return final response with all content blocks

        Args:
            request: The message request
            bedrock_service: Service for calling Bedrock
            request_id: Unique request ID
            service_tier: User's service tier
            container_id: Optional container ID for session reuse
            anthropic_beta: Beta header value

        Returns:
            Tuple of (MessageResponse, ContainerInfo or None)
        """
        logger.info(f"[Standalone] Handling request {request_id}, container_id={container_id}")

        # Filter out the standalone beta header - Bedrock doesn't recognize it
        # This is a local feature, not passed to Bedrock
        filtered_beta = self._filter_beta_header(anthropic_beta)
        logger.info(f"[Standalone] Filtered beta header: {anthropic_beta} -> {filtered_beta}")

        # Get or create session
        session = await self._get_or_create_session(container_id)
        logger.info(f"[Standalone] Using session {session.session_id}")

        # Accumulate all content blocks for full trace
        all_content: List[Any] = []
        total_input_tokens = 0
        total_output_tokens = 0

        # Track messages for continuation (type: List[Any] to allow both Message and dict)
        messages: List[Any] = list(request.messages)

        iteration = 0
        final_response = None
        _ = service_tier  # Mark as used (reserved for future tier-based limits)

        while iteration < MAX_ITERATIONS:
            iteration += 1
            logger.info(f"[Standalone] Iteration {iteration}/{MAX_ITERATIONS}")

            # Build request for this iteration
            # Replace code_execution marker with actual tool definitions
            standalone_tools = self._build_tools_for_request(request.tools)

            # Note: MessageRequest accepts dicts via Pydantic validation
            iter_request = MessageRequest(
                model=request.model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=request.max_tokens,
                system=request.system,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k,
                stop_sequences=request.stop_sequences,
                stream=False,  # Always non-streaming for standalone
                tools=standalone_tools,
                tool_choice=request.tool_choice,
                thinking=request.thinking,
                metadata=request.metadata,
            )

            # Call Bedrock (without the standalone beta header)
            try:
                response = await bedrock_service.invoke_model(
                    iter_request,
                    anthropic_beta=filtered_beta,
                )
            except Exception as e:
                logger.error(f"[Standalone] Bedrock call failed: {e}")
                raise

            # Track tokens
            if response.usage:
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens

            # Extract content and find server_tool_use blocks
            response_content = response.content if hasattr(response, 'content') else []
            server_tool_uses = self._find_server_tool_use(response_content)

            logger.info(f"[Standalone] Found {len(server_tool_uses)} server_tool_use blocks")

            # Convert tool_use to server_tool_use and add to accumulated trace
            converted_content = self._convert_to_server_tool_use(response_content)
            all_content.extend(converted_content)

            # Check if we should continue
            if not server_tool_uses or response.stop_reason != "tool_use":
                logger.info(f"[Standalone] Loop complete, stop_reason={response.stop_reason}")
                final_response = response
                break

            # Execute server tools
            tool_results = []
            for tool_use in server_tool_uses:
                result = await self._execute_server_tool(tool_use, session)
                tool_results.append(result)
                all_content.append(result)

            # Build continuation messages
            messages = self._build_continuation_messages(
                messages,
                response_content,
                tool_results,
            )

        # Build final response with full trace
        container_info = ContainerInfo(
            id=session.session_id,
            expires_at=session.expires_at.isoformat(),
        )

        final_message = MessageResponse(
            id=f"msg_{uuid4().hex[:24]}",
            type="message",
            role="assistant",
            content=all_content,
            model=request.model,
            stop_reason=final_response.stop_reason if final_response else "end_turn",
            stop_sequence=final_response.stop_sequence if final_response else None,
            usage=Usage(
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            ),
        )

        return final_message, container_info

    async def _get_or_create_session(
        self,
        container_id: Optional[str],
    ) -> StandaloneSandboxSession:
        """Get existing session or create a new one."""
        if container_id:
            session = self.sandbox_executor.get_session(container_id)
            if session:
                logger.info(f"[Standalone] Reusing session {container_id}")
                return session
            logger.info(f"[Standalone] Session {container_id} not found, creating new")

        # Create new session
        session = await self.sandbox_executor.create_session()
        return session

    def _find_server_tool_use(self, content: List[Any]) -> List[Dict[str, Any]]:
        """
        Find tool_use blocks for bash operations.

        In standalone mode, Bedrock returns regular tool_use blocks (not server_tool_use).
        We detect them by checking if the tool name is bash_code_execution.

        Note: text_editor_code_execution is disabled (requires Files API).

        Args:
            content: List of content blocks from response

        Returns:
            List of tool_use dicts for standalone execution
        """
        standalone_tools = []
        # Only bash_code_execution is enabled
        # text_editor_code_execution requires Files API
        standalone_tool_names = ("bash_code_execution",)

        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, 'model_dump') else {}
            )

            block_type = block_dict.get("type", "")
            # Check both server_tool_use (Anthropic native) and tool_use (Bedrock)
            if block_type in ("server_tool_use", "tool_use"):
                name = block_dict.get("name", "")
                if name in standalone_tool_names:
                    standalone_tools.append(block_dict)

        return standalone_tools

    def _convert_to_server_tool_use(self, content: List[Any]) -> List[Any]:
        """
        Convert tool_use blocks to server_tool_use format for standalone tools.

        In standalone mode, Bedrock returns regular tool_use blocks, but the
        official Anthropic API format uses server_tool_use for server-executed tools.

        Args:
            content: List of content blocks from response

        Returns:
            List of content blocks with standalone tool_use converted to server_tool_use
        """
        standalone_tool_names = ("bash_code_execution", "text_editor_code_execution")
        converted = []

        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, 'model_dump') else {}
            )

            block_type = block_dict.get("type", "")

            # Convert tool_use to server_tool_use for standalone tools
            if block_type == "tool_use":
                name = block_dict.get("name", "")
                if name in standalone_tool_names:
                    # Convert to server_tool_use format
                    converted.append({
                        "type": "server_tool_use",
                        "id": block_dict.get("id", ""),
                        "name": name,
                        "input": block_dict.get("input", {}),
                    })
                else:
                    # Keep non-standalone tools as-is
                    converted.append(block_dict)
            else:
                # Keep other block types as-is
                converted.append(block_dict)

        return converted

    async def _execute_server_tool(
        self,
        tool_use: Dict[str, Any],
        session: StandaloneSandboxSession,
    ) -> Dict[str, Any]:
        """
        Execute a server tool (bash or text_editor) in sandbox.

        Args:
            tool_use: The server_tool_use block
            session: Sandbox session

        Returns:
            Tool result content block
        """
        tool_name = tool_use.get("name")
        tool_id = tool_use.get("id", f"srvtoolu_{uuid4().hex[:24]}")
        tool_input = tool_use.get("input", {})

        logger.info(f"[Standalone] Executing {tool_name} (id={tool_id})")

        if tool_name == "bash_code_execution":
            return await self._execute_bash(str(tool_id), tool_input, session)
        elif tool_name == "text_editor_code_execution":
            return await self._execute_text_editor(str(tool_id), tool_input, session)
        else:
            # Unknown tool - return error
            return {
                "type": "bash_code_execution_tool_result",
                "tool_use_id": tool_id,
                "content": {
                    "type": "bash_code_execution_result",
                    "stdout": "",
                    "stderr": f"Unknown tool: {tool_name}",
                    "return_code": 1,
                }
            }

    async def _execute_bash(
        self,
        tool_id: str,
        tool_input: Dict[str, Any],
        session: StandaloneSandboxSession,
    ) -> Dict[str, Any]:
        """Execute bash command."""
        command = tool_input.get("command", "")
        restart = tool_input.get("restart", False)

        logger.info(f"[Standalone] Bash: {command[:100]}...")

        try:
            result = await self.sandbox_executor.execute_bash(
                session,
                command,
                restart=restart,
            )

            return {
                "type": "bash_code_execution_tool_result",
                "tool_use_id": tool_id,
                "content": {
                    "type": "bash_code_execution_result",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "return_code": result.return_code,
                }
            }
        except Exception as e:
            logger.error(f"[Standalone] Bash execution error: {e}")
            return {
                "type": "bash_code_execution_tool_result",
                "tool_use_id": tool_id,
                "content": {
                    "type": "bash_code_execution_result",
                    "stdout": "",
                    "stderr": str(e),
                    "return_code": 1,
                }
            }

    async def _execute_text_editor(
        self,
        tool_id: str,
        tool_input: Dict[str, Any],
        session: StandaloneSandboxSession,
    ) -> Dict[str, Any]:
        """Execute text editor command."""
        command = tool_input.get("command", "view")
        path = tool_input.get("path", "")

        logger.info(f"[Standalone] TextEditor: {command} {path}")

        try:
            result = await self.sandbox_executor.execute_text_editor(
                session,
                command=command,
                path=path,
                view_range=tool_input.get("view_range"),
                file_text=tool_input.get("file_text"),
                old_str=tool_input.get("old_str"),
                new_str=tool_input.get("new_str"),
            )

            # Build result content based on operation type
            result_content: Dict[str, Any] = {
                "type": "text_editor_code_execution_result",
            }

            if result.error_code:
                result_content["error_code"] = result.error_code
            else:
                # Add fields based on command type
                if command == "view":
                    result_content["file_type"] = result.file_type
                    result_content["content"] = result.content
                    result_content["numLines"] = result.num_lines
                    result_content["startLine"] = result.start_line
                    result_content["totalLines"] = result.total_lines
                elif command == "create":
                    result_content["is_file_update"] = result.is_file_update
                elif command == "str_replace":
                    result_content["oldStart"] = result.old_start
                    result_content["oldLines"] = result.old_lines
                    result_content["newStart"] = result.new_start
                    result_content["newLines"] = result.new_lines
                    result_content["lines"] = result.lines

            return {
                "type": "text_editor_code_execution_tool_result",
                "tool_use_id": tool_id,
                "content": result_content,
            }

        except Exception as e:
            logger.error(f"[Standalone] TextEditor execution error: {e}")
            return {
                "type": "text_editor_code_execution_tool_result",
                "tool_use_id": tool_id,
                "content": {
                    "type": "text_editor_code_execution_result",
                    "error_code": "unavailable",
                }
            }

    def _convert_result_to_tool_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert custom tool result types to standard tool_result format.

        Claude/Bedrock expects standard tool_result type for continuations:
        {"type": "tool_result", "tool_use_id": "...", "content": "..."}

        Args:
            result: Custom result dict (bash_code_execution_tool_result, etc.)

        Returns:
            Standard tool_result dict
        """
        result_type = result.get("type", "")
        tool_use_id = result.get("tool_use_id", "")

        if result_type == "bash_code_execution_tool_result":
            content = result.get("content", {})
            stdout = content.get("stdout", "")
            stderr = content.get("stderr", "")
            return_code = content.get("return_code", 0)

            # Format as text content
            text_parts = []
            if stdout:
                text_parts.append(f"stdout:\n{stdout}")
            if stderr:
                text_parts.append(f"stderr:\n{stderr}")
            text_parts.append(f"return_code: {return_code}")

            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "\n".join(text_parts),
                "is_error": return_code != 0,
            }

        elif result_type == "text_editor_code_execution_tool_result":
            content = result.get("content", {})
            error_code = content.get("error_code")

            if error_code:
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"Error: {error_code}",
                    "is_error": True,
                }

            # Build result text based on operation type
            text_parts = []
            if content.get("content") is not None:  # view result
                text_parts.append(content["content"])
                if content.get("num_lines") is not None:
                    text_parts.append(f"\n(Lines {content.get('start_line', 1)}-{content.get('start_line', 1) + content.get('num_lines', 0) - 1} of {content.get('total_lines', '?')})")
            elif content.get("is_file_update") is not None:  # create result
                text_parts.append(f"File {'updated' if content['is_file_update'] else 'created'} successfully")
            elif content.get("old_start") is not None:  # str_replace result
                text_parts.append(f"Replaced lines {content['old_start']}-{content['old_start'] + content.get('old_lines', 0) - 1}")
                if content.get("lines"):
                    text_parts.append("\nDiff:\n" + "\n".join(content["lines"]))
            else:
                text_parts.append("Operation completed")

            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "".join(text_parts),
            }

        # Unknown type - pass through
        return result

    def _build_continuation_messages(
        self,
        original_messages: List[Any],
        assistant_content: List[Any],
        tool_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Build messages for continuation request with tool results.

        Args:
            original_messages: Previous messages
            assistant_content: Content from assistant's response
            tool_results: Results from tool execution

        Returns:
            Updated messages list for next iteration
        """
        # Convert original messages to dicts
        messages = []
        for msg in original_messages:
            if isinstance(msg, dict):
                messages.append(msg)
            elif hasattr(msg, 'model_dump'):
                messages.append(msg.model_dump())
            else:
                messages.append({"role": msg.role, "content": msg.content})

        # Add assistant message with response content
        assistant_content_list = []
        for block in assistant_content:
            if isinstance(block, dict):
                assistant_content_list.append(block)
            elif hasattr(block, 'model_dump'):
                assistant_content_list.append(block.model_dump())
            else:
                assistant_content_list.append(block)

        messages.append({
            "role": "assistant",
            "content": assistant_content_list,
        })

        # Add user message with tool results
        # Convert custom result types to standard tool_result format
        user_content = []
        for result in tool_results:
            converted = self._convert_result_to_tool_result(result)
            user_content.append(converted)

        messages.append({
            "role": "user",
            "content": user_content,
        })

        return messages


# Global singleton instance
_standalone_service: Optional[StandaloneCodeExecutionService] = None


def get_standalone_service() -> StandaloneCodeExecutionService:
    """Get the global standalone code execution service instance."""
    global _standalone_service
    if _standalone_service is None:
        _standalone_service = StandaloneCodeExecutionService()
    return _standalone_service
