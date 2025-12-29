"""
Programmatic Tool Calling (PTC) Service.

Orchestrates the PTC flow:
1. Detect PTC requests based on beta header and tools
2. Manage conversation with Claude via Bedrock
3. Execute code in Docker sandbox
4. Return tool calls to client for execution
5. Resume sandbox execution with tool results
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.core.config import settings
from app.schemas.anthropic import MessageRequest, MessageResponse
from app.schemas.ptc import (
    PTC_BETA_HEADER,
    PTC_TOOL_TYPE,
    PTC_ALLOWED_CALLER,
    ContainerInfo,
    PTCExecutionState,
)
from app.services.ptc import (
    PTCSandboxExecutor,
    SandboxConfig,
    SandboxSession,
    ToolCallRequest,
    BatchToolCallRequest,
    ExecutionResult,
    DockerNotAvailableError,
    SandboxError,
    PendingToolCall,
)

logger = logging.getLogger(__name__)


def _filter_non_direct_tool_calls(messages: List[Any]) -> List[Any]:
    """
    Filter out non-direct tool calls and their corresponding results from messages.

    In PTC mode, tool calls with caller.type != "direct" are executed by the sandbox,
    not by Claude directly. These should NOT be included in conversation history
    sent to Claude.

    This function:
    1. Identifies tool_use blocks with caller.type != "direct" (or caller.type == "code_execution_20250825")
    2. Removes those tool_use blocks from assistant messages
    3. Removes corresponding tool_result blocks from user messages
    4. Also removes server_tool_use blocks (code_execution internal blocks)

    Args:
        messages: List of message dicts with role and content

    Returns:
        Filtered messages list
    """
    # First pass: collect tool_use IDs that should be filtered out
    non_direct_tool_ids = set()

    for message in messages:
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content", [])
        elif hasattr(message, "role"):
            role = message.role
            content = message.content if hasattr(message, "content") else []
        else:
            continue

        if role != "assistant":
            continue

        if isinstance(content, str):
            continue

        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )

            block_type = block_dict.get("type")

            # Filter server_tool_use blocks (code_execution internal)
            if block_type == "server_tool_use":
                block_id = block_dict.get("id")
                if block_id:
                    non_direct_tool_ids.add(block_id)

            # Filter tool_use blocks with non-direct caller
            if block_type == "tool_use":
                caller = block_dict.get("caller")
                if caller:
                    caller_type = caller.get("type") if isinstance(caller, dict) else (
                        caller.type if hasattr(caller, "type") else None
                    )
                    # If caller exists and is NOT "direct", filter it out
                    if caller_type and caller_type != "direct":
                        block_id = block_dict.get("id")
                        if block_id:
                            non_direct_tool_ids.add(block_id)

    if not non_direct_tool_ids:
        return messages

    logger.debug(f"[PTC] Filtering {len(non_direct_tool_ids)} non-direct tool call IDs from messages")

    # Second pass: filter messages
    filtered_messages = []

    for message in messages:
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content", [])
        elif hasattr(message, "role"):
            role = message.role
            content = message.content if hasattr(message, "content") else []
        else:
            filtered_messages.append(message)
            continue

        if isinstance(content, str):
            filtered_messages.append(message)
            continue

        # Filter content blocks
        filtered_content = []
        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )

            block_type = block_dict.get("type")

            # Skip server_tool_use blocks entirely
            if block_type == "server_tool_use":
                continue

            # Filter tool_use blocks
            if block_type == "tool_use":
                block_id = block_dict.get("id")
                if block_id in non_direct_tool_ids:
                    continue

            # Filter tool_result blocks for non-direct tool calls
            if block_type == "tool_result":
                tool_use_id = block_dict.get("tool_use_id")
                if tool_use_id in non_direct_tool_ids:
                    continue

            # Keep this block
            filtered_content.append(block)

        # Only add message if it has content
        if filtered_content:
            if isinstance(message, dict):
                filtered_messages.append({
                    **message,
                    "content": filtered_content
                })
            else:
                # For Pydantic models, create a new dict
                msg_dict = message.model_dump() if hasattr(message, "model_dump") else dict(message)
                msg_dict["content"] = filtered_content
                filtered_messages.append(msg_dict)

    return filtered_messages


class PTCService:
    """
    Service for handling Programmatic Tool Calling requests.

    This service manages the complex PTC flow where:
    - Claude generates code that calls tools
    - Code runs in a Docker sandbox
    - Tool calls are intercepted and returned to the client
    - Client executes tools and returns results
    - Sandbox continues execution with results
    """

    def __init__(self):
        self._sandbox_executor: Optional[PTCSandboxExecutor] = None
        self._execution_states: Dict[str, PTCExecutionState] = {}
        self._execution_generators: Dict[str, Any] = {}  # Store active generators

    @property
    def sandbox_executor(self) -> PTCSandboxExecutor:
        """Lazy-load sandbox executor."""
        if self._sandbox_executor is None:
            config = SandboxConfig(
                image=settings.ptc_sandbox_image,
                memory_limit=settings.ptc_memory_limit,
                timeout_seconds=settings.ptc_execution_timeout,
                network_disabled=settings.ptc_network_disabled,
                session_timeout_seconds=settings.ptc_session_timeout,
            )
            self._sandbox_executor = PTCSandboxExecutor(config)
            self._sandbox_executor.start_cleanup_task()
        return self._sandbox_executor

    def is_docker_available(self) -> bool:
        """Check if Docker is available for PTC."""
        try:
            return self.sandbox_executor.is_docker_available()
        except Exception:
            return False

    @staticmethod
    def is_ptc_request(request: MessageRequest, beta_header: Optional[str]) -> bool:
        """
        Check if request is a PTC request.

        Conditions:
        1. Beta header contains 'advanced-tool-use-2025-11-20'
        2. Tools include code_execution_20250825 type
        3. PTC is enabled in config
        """
        if not settings.enable_programmatic_tool_calling:
            return False

        # Check beta header
        if not beta_header or PTC_BETA_HEADER not in beta_header:
            return False

        # Check for code_execution tool
        if not request.tools:
            return False

        for tool in request.tools:
            # Handle both dict and Pydantic model
            if isinstance(tool, dict):
                if tool.get("type") == PTC_TOOL_TYPE:
                    return True
            elif hasattr(tool, "type") and tool.type == PTC_TOOL_TYPE:
                return True

        return False

    @staticmethod
    def get_ptc_tools(request: MessageRequest) -> Tuple[List[dict], List[dict]]:
        """
        Separate PTC tools from regular tools.

        Returns:
            Tuple of (code_execution_tools, ptc_callable_tools)
            - code_execution_tools: Tools that are code_execution type
            - ptc_callable_tools: Regular tools that can be called from code execution
        """
        code_execution_tools = []
        ptc_callable_tools = []

        for tool in (request.tools or []):
            tool_dict = tool if isinstance(tool, dict) else tool.model_dump()

            if tool_dict.get("type") == PTC_TOOL_TYPE:
                code_execution_tools.append(tool_dict)
            else:
                # Check if tool has allowed_callers
                allowed_callers = tool_dict.get("allowed_callers", ["direct"])
                if PTC_ALLOWED_CALLER in allowed_callers:
                    ptc_callable_tools.append(tool_dict)

        return code_execution_tools, ptc_callable_tools

    def _build_execute_code_tool(self, ptc_tools: List[dict]) -> dict:
        """
        Build the execute_code tool definition for Claude.

        This replaces the server-side code_execution tool with a regular
        tool that Claude can call, which we then handle in the sandbox.
        """
        # Build tool documentation
        tool_docs = []
        for tool in ptc_tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            schema = tool.get("input_schema", {})
            tool_docs.append(f"- {name}: {desc}\n  Parameters: {json.dumps(schema)}")

        tools_doc = "\n".join(tool_docs) if tool_docs else "No tools available"

        return {
            "name": "execute_code",
            "description": f"""Execute Python code in a sandboxed environment.

The code can call the following async tool functions:
{tools_doc}

Important:
- All tool calls must use `await`, e.g., `result = await query_database(sql="SELECT * FROM users")`
- Use `print()` to output results you want to see
- Code runs in an isolated environment without network access
- Only the print output will be returned

Performance optimization - PARALLEL EXECUTION:
When you need to call the same tool multiple times with different parameters (e.g., fetching data for multiple items), ALWAYS use asyncio.gather for parallel execution instead of sequential loops:

BAD (slow, sequential):
```python
results = []
for item_id in item_ids:
    result = await get_item(id=item_id)
    results.append(result)
```

GOOD (fast, parallel):
```python
import asyncio
tasks = [get_item(id=item_id) for item_id in item_ids]
results = await asyncio.gather(*tasks)
```

This significantly improves performance by executing multiple tool calls concurrently.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute. Use await for tool calls. Use asyncio.gather for parallel tool calls."
                    }
                },
                "required": ["code"]
            }
        }

    def prepare_bedrock_request(
        self,
        request: MessageRequest,
        ptc_callable_tools: List[dict]
    ) -> MessageRequest:
        """
        Prepare request for Bedrock by replacing PTC tools with execute_code.

        This transforms the request to remove server-side code_execution tool
        and add our own execute_code tool that we handle locally.
        """
        # Build new tools list
        new_tools = []

        # Add execute_code tool
        execute_code_tool = self._build_execute_code_tool(ptc_callable_tools)
        new_tools.append(execute_code_tool)

        # Add any "direct" callable tools
        for tool in (request.tools or []):
            tool_dict = tool if isinstance(tool, dict) else tool.model_dump()

            # Skip code_execution server tool
            if tool_dict.get("type") == PTC_TOOL_TYPE:
                continue

            # Check if tool is direct-callable
            allowed_callers = tool_dict.get("allowed_callers", ["direct"])
            if "direct" in allowed_callers:
                # Remove allowed_callers field for Bedrock
                tool_copy = {k: v for k, v in tool_dict.items() if k != "allowed_callers"}
                new_tools.append(tool_copy)

        # Create modified request
        request_dict = request.model_dump()
        request_dict["tools"] = new_tools

        # Append PTC system prompt for parallel execution guidance
        ptc_system_prompt = self._build_ptc_system_prompt(ptc_callable_tools)
        existing_system = request_dict.get("system")

        if existing_system:
            if isinstance(existing_system, str):
                request_dict["system"] = existing_system + "\n\n" + ptc_system_prompt
            elif isinstance(existing_system, list):
                # System is a list of content blocks
                request_dict["system"] = existing_system + [{"type": "text", "text": ptc_system_prompt}]
        else:
            request_dict["system"] = ptc_system_prompt

        return MessageRequest(**request_dict)

    def _build_ptc_system_prompt(self, ptc_tools: List[dict]) -> str:
        """Build system prompt additions for PTC mode."""
        # Build tool documentation
        tool_docs = []
        for tool in ptc_tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            schema = tool.get("input_schema", {})
            properties = schema.get("properties", {})
            params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in properties.items())
            tool_docs.append(f"- `{name}({params})`: {desc}")

        tools_doc = "\n".join(tool_docs) if tool_docs else "No tools available"

        return f"""## Code Execution Environment

You have access to the `execute_code` tool which runs Python code in a sandboxed environment. Within your code, you can call the following async tool functions:

{tools_doc}

## Usage

When you need to execute multi-step tasks, use the `execute_code` tool to write Python code.

### Key Rules:
1. All tool calls must use `await`, for example: `result = await query_sales(region="East")`
2. Use `print()` to output results - this is the only way for you to get execution results
3. You can perform data processing, filtering, aggregation, and conditional logic in your code
4. After code execution completes, you will see the content output by print

## CRITICAL: Stateless Execution Environment

**IMPORTANT: Each `execute_code` call runs in a FRESH, ISOLATED environment.**

- Variables, data, and state from previous code executions DO NOT persist
- Each code block starts with a completely clean slate
- You CANNOT reference variables defined in previous `execute_code` calls

### What This Means:

**WRONG** - Assuming variables persist across calls:
```python
# First execute_code call
products = await get_inventory(warehouse="NYC")
print(products)

# Second execute_code call - THIS WILL FAIL!
# products does not exist here!
for item in products:  # NameError: 'products' is not defined
    details = await get_product_details(sku=item['sku'])
```

**CORRECT** - Complete all work in a SINGLE code block (STRONGLY PREFERRED):
```python
import json
import asyncio

# Do EVERYTHING in one code block
inventory_data = await get_inventory(warehouse="NYC")
products = json.loads(inventory_data)

# Continue processing in the same block
detail_tasks = [get_product_details(sku=p['sku']) for p in products]
details = await asyncio.gather(*detail_tasks)

# Analyze and print final results
for product, detail in zip(products, details):
    print(f"{{product['name']}}: {{detail}}")
```

**CORRECT** - If multiple blocks unavoidable, re-fetch data:
```python
import json

# In a NEW code block, re-fetch the data you need
inventory_data = await get_inventory(warehouse="NYC")
products = json.loads(inventory_data)

# Now continue processing
detail_tasks = [get_product_details(sku=p['sku']) for p in products]
# ...
```

## Best Practices for Coding

### 1. Complete Tasks in One Block (MOST IMPORTANT)

For multi-step tasks, write ONE code block that accomplishes everything:

```python
import json
import asyncio

# Step 1: Get all orders from the past week
orders_data = await get_recent_orders(days=7)
orders = json.loads(orders_data)
print(f"Processing {{len(orders)}} orders")

# Step 2: Get customer info for all orders in parallel
customer_ids = list(set(order['customer_id'] for order in orders))
customer_tasks = [get_customer(customer_id=cid) for cid in customer_ids]
customer_results = await asyncio.gather(*customer_tasks)
customers = {{cid: json.loads(data) for cid, data in zip(customer_ids, customer_results)}}

# Step 3: Find high-value orders from premium customers
HIGH_VALUE_THRESHOLD = 1000
premium_high_value = []
for order in orders:
    customer = customers[order['customer_id']]
    if customer['tier'] == 'premium' and order['total'] > HIGH_VALUE_THRESHOLD:
        premium_high_value.append({{
            'order_id': order['id'],
            'customer_name': customer['name'],
            'total': order['total']
        }})

# Step 4: Get shipping status for these orders
if premium_high_value:
    shipping_tasks = [get_shipping_status(order_id=o['order_id']) for o in premium_high_value]
    shipping_results = await asyncio.gather(*shipping_tasks)
  
    print("\nPremium customers with high-value orders:")
    for order_info, shipping_json in zip(premium_high_value, shipping_results):
        shipping = json.loads(shipping_json)
        print(f"  Order {{order_info['order_id']}}: ${{order_info['total']:,.2f}} - {{order_info['customer_name']}} - Status: {{shipping['status']}}")
else:
    print("No high-value orders from premium customers found")
```

### 2. Parallel Execution with asyncio.gather()

When calling the same tool for multiple items, always use parallel execution:

```python
import asyncio
import json

# Get health metrics for multiple servers in parallel
server_ids = ["srv-001", "srv-002", "srv-003", "srv-004"]
health_tasks = [check_server_health(server_id=sid) for sid in server_ids]
health_results = await asyncio.gather(*health_tasks)

# Process results
unhealthy = []
for server_id, health_json in zip(server_ids, health_results):
    health = json.loads(health_json)
    if health['cpu_usage'] > 90 or health['memory_usage'] > 85:
        unhealthy.append(f"{{server_id}}: CPU={{health['cpu_usage']}}%, MEM={{health['memory_usage']}}%")

if unhealthy:
    print("Servers needing attention:")
    for s in unhealthy:
        print(f"{{s}}")
else:
    print("All servers healthy")
```

### 3. Conditional Logic Within One Block

Handle all branching logic in a single execution:

```python
import json

# Get account status first
account_data = await get_account(account_id="ACC-12345")
account = json.loads(account_data)

if account['status'] == 'suspended':
    # Get suspension details
    suspension_info = await get_suspension_details(account_id="ACC-12345")
    print(f"Account suspended: {{json.loads(suspension_info)['reason']}}")
  
elif account['balance'] < 0:
    # Get payment history for accounts with negative balance
    payments = await get_payment_history(account_id="ACC-12345", limit=5)
    print(f"Negative balance. Recent payments: {{payments}}")
  
else:
    # Get recommendations for active accounts
    recommendations = await get_recommendations(account_id="ACC-12345")
    print(f"Account active. Recommendations: {{recommendations}}")
```

### 4. Early Termination Pattern

Stop processing once you find what you need:

```python
import json

regions = ["us-east", "us-west", "eu-central", "ap-southeast"]
available_region = None

for region in regions:
    capacity_data = await check_capacity(region=region)
    capacity = json.loads(capacity_data)
  
    if capacity['available_slots'] >= 10:
        available_region = region
        print(f"Found suitable region: {{region}} with {{capacity['available_slots']}} slots")
        break
    else:
        print(f"{{region}}: only {{capacity['available_slots']}} slots available")

if not available_region:
    print("No region with sufficient capacity found")
```

### 5. Aggregation and Analysis

Fetch data and perform complex analysis in one block:

```python
import json
import asyncio
from collections import defaultdict

# Get all transactions for the quarter
transactions_data = await get_transactions(quarter="Q3", year=2024)
transactions = json.loads(transactions_data)

# Aggregate by category
category_totals = defaultdict(float)
category_counts = defaultdict(int)

for txn in transactions:
    category_totals[txn['category']] += txn['amount']
    category_counts[txn['category']] += 1

# Find categories exceeding budget
budgets = {{'marketing': 50000, 'operations': 75000, 'travel': 20000, 'equipment': 30000}}

print("Q3 Spending Analysis:")
print("-" * 50)
for category, total in sorted(category_totals.items(), key=lambda x: -x[1]):
    budget = budgets.get(category, 0)
    status = "OVER" if total > budget else "OK"
    variance = total - budget
    print(f"{{category:15}} ${{total:>10,.2f}} / ${{budget:>10,.2f}} ({{status}}, {{variance:+,.2f}})")
```

## When Multiple Code Blocks Are Unavoidable

If a task requires user decisions between steps or is too complex for one block:

1. **Print clear, structured output** from the first block
2. **Re-fetch or reconstruct data** in subsequent blocks - never assume variables exist
3. **Prefer re-fetching** over reconstructing from printed output (more reliable)

```python
# If you need another code block, ALWAYS start fresh:
import json

# Re-fetch the data - don't assume anything exists from before
inventory_data = await get_inventory(warehouse="NYC")
products = json.loads(inventory_data)

# Now continue with your analysis...
```

## Docker Sandbox Features
- Secure, isolated execution environment
- **Each execution starts fresh with no state from previous executions**
- Network disabled for security
- Resource limits enforced (memory, CPU)
- Timeout protection

## Pre-Code Checklist

Before writing code, verify:
- [ ] I am NOT referencing variables from a previous `execute_code` call
- [ ] I have included all necessary imports (`json`, `asyncio`, etc.)
- [ ] I am using `await` for all async tool calls
- [ ] I am using `json.loads()` to parse tool return values
- [ ] I am using `print()` to output all results I need to see
- [ ] I am completing as much as possible in this single code block
"""

    async def handle_ptc_request(
        self,
        request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        container_id: Optional[str] = None
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Handle a PTC request.

        This is the main entry point for PTC requests. It:
        1. Prepares the request for Bedrock
        2. Calls Claude
        3. If Claude returns execute_code, runs code in sandbox
        4. Returns tool_use if sandbox needs external tool
        5. Otherwise returns final response

        Args:
            request: The original request
            bedrock_service: Bedrock service for calling Claude
            request_id: Request ID
            service_tier: Bedrock service tier
            container_id: Optional container ID for session reuse

        Returns:
            Tuple of (response, container_info)
        """
        # Check Docker availability
        if not self.is_docker_available():
            raise DockerNotAvailableError(
                "Programmatic Tool Calling requires Docker which is not available. "
                "Please ensure Docker is running."
            )

        # Get PTC tools
        _, ptc_callable_tools = self.get_ptc_tools(request)

        # Prepare request for Bedrock
        bedrock_request = self.prepare_bedrock_request(request, ptc_callable_tools)

        # Get or create sandbox session
        session = await self._get_or_create_session(container_id, ptc_callable_tools)

        try:
            # Call Bedrock
            response = await bedrock_service.invoke_model(
                bedrock_request, request_id, service_tier
            )

            # Check if Claude called execute_code
            execute_code_call = self._find_execute_code_call(response)

            if execute_code_call:
                # Execute code in sandbox
                return await self._handle_code_execution(
                    execute_code_call,
                    response,
                    session,
                    request,
                    bedrock_service,
                    request_id,
                    service_tier,
                    ptc_callable_tools
                )
            else:
                # No code execution, return response with container info
                # Add caller: {type: "direct"} to any direct tool_use blocks
                response = self._add_direct_caller_to_tool_use(response)
                container_info = ContainerInfo(
                    id=session.session_id,
                    expires_at=session.expires_at.isoformat()
                )
                return response, container_info

        except Exception as e:
            logger.error(f"Error handling PTC request: {e}")
            raise

    async def _get_or_create_session(
        self,
        container_id: Optional[str],
        tools: List[dict]
    ) -> SandboxSession:
        """Get existing session or create new one."""
        session = None

        if container_id:
            session = self.sandbox_executor.get_session(container_id)

        if session is None:
            # Create new session with tool definitions
            tool_defs = [
                {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", {})
                }
                for t in tools
            ]
            session = await self.sandbox_executor.create_session(tool_defs)

        return session

    def _find_execute_code_call(self, response: MessageResponse) -> Optional[dict]:
        """Find execute_code tool call in response."""
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                if hasattr(block, "name") and block.name == "execute_code":
                    return {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input if hasattr(block, "input") else {}
                    }
            elif isinstance(block, dict):
                if block.get("type") == "tool_use" and block.get("name") == "execute_code":
                    return block

        return None

    async def _handle_code_execution(
        self,
        execute_code_call: dict,
        claude_response: MessageResponse,
        session: SandboxSession,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        ptc_callable_tools: List[dict]
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Handle code execution in sandbox.

        When Claude calls execute_code:
        1. Run code in sandbox
        2. If sandbox calls external tool, return tool_use to client
        3. If code completes, send result back to Claude
        """
        code = execute_code_call.get("input", {}).get("code", "")
        code_execution_tool_id = f"srvtoolu_{uuid4().hex[:12]}"

        # Check if there's a pending tool call for this session
        # If so, the container is waiting for a tool result - we can't send new code
        pending_state = self._execution_states.get(session.session_id)
        if pending_state or session.pending_tool_call or session.is_busy:
            reason = []
            if pending_state:
                reason.append(f"pending tool call ({pending_state.pending_tool_name})")
            if session.pending_tool_call:
                reason.append(f"session pending_tool_call ({session.pending_tool_call.tool_name})")
            if session.is_busy:
                reason.append("session is_busy")

            logger.warning(
                f"Session {session.session_id} in inconsistent state: {', '.join(reason)}. "
                "Creating new session."
            )
            # Clean up the pending state - the old execution is abandoned
            self._cleanup_execution_state(session.session_id)
            # Close the old session and create a new one - container is in inconsistent state
            await self.sandbox_executor.close_session(session.session_id)
            # Create fresh session
            tool_defs = [
                {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", {})
                }
                for t in ptc_callable_tools
            ]
            session = await self.sandbox_executor.create_session(tool_defs)
            logger.info(f"Created new session {session.session_id} after cleaning up stale state")

        logger.info(f"Executing code in sandbox:\n{code}")

        # Execute code in sandbox (using async generator pattern)
        gen = self.sandbox_executor.execute_code(code, session)

        try:
            # Get first result (either tool call, batch of tool calls, or final result)
            result = await gen.__anext__()

            while isinstance(result, (ToolCallRequest, BatchToolCallRequest)):
                # Tool call(s) requested - return to client
                container_info = ContainerInfo(
                    id=session.session_id,
                    expires_at=session.expires_at.isoformat()
                )

                if isinstance(result, BatchToolCallRequest):
                    # Multiple parallel tool calls
                    logger.info(f"[PTC] Batch of {len(result)} tool calls")
                    first_call = result.requests[0]
                    pending_call_ids = [r.call_id for r in result.requests]

                    # Store execution state for resume
                    state = PTCExecutionState(
                        session_id=session.session_id,
                        code_execution_tool_id=code_execution_tool_id,
                        code=code,  # Store actual code for response
                        pending_tool_call_id=first_call.call_id,  # Track first call
                        pending_tool_name=first_call.tool_name,
                        pending_tool_input=first_call.arguments,
                        pending_batch_call_ids=pending_call_ids,  # Track all call IDs
                    )
                    self._execution_states[session.session_id] = state
                    self._execution_generators[session.session_id] = gen

                    # Mark session as having pending tool calls
                    session.pending_tool_call = PendingToolCall(
                        call_id=first_call.call_id,
                        tool_name=first_call.tool_name,
                        arguments=first_call.arguments,
                        session_id=session.session_id,
                        code_execution_tool_id=code_execution_tool_id
                    )

                    # Build response with multiple tool_use blocks
                    tool_use_response = self._build_batch_tool_use_response(
                        result,
                        code_execution_tool_id,
                        claude_response,
                        container_info,
                        code=code
                    )

                    return tool_use_response, container_info

                else:
                    # Single tool call (original behavior)
                    # Store execution state for resume
                    state = PTCExecutionState(
                        session_id=session.session_id,
                        code_execution_tool_id=code_execution_tool_id,
                        code=code,  # Store actual code for response
                        pending_tool_call_id=result.call_id,
                        pending_tool_name=result.tool_name,
                        pending_tool_input=result.arguments,
                    )
                    self._execution_states[session.session_id] = state
                    self._execution_generators[session.session_id] = gen

                    # Also mark the session itself as having a pending tool call
                    session.pending_tool_call = PendingToolCall(
                        call_id=result.call_id,
                        tool_name=result.tool_name,
                        arguments=result.arguments,
                        session_id=session.session_id,
                        code_execution_tool_id=code_execution_tool_id
                    )

                    # Build response with tool_use and caller info
                    tool_use_response = self._build_tool_use_response(
                        result,
                        code_execution_tool_id,
                        claude_response,
                        container_info,
                        code=code
                    )

                    return tool_use_response, container_info

            # Code completed - result is ExecutionResult
            if isinstance(result, ExecutionResult):
                # Close the generator to trigger its finally block (clears is_busy)
                await gen.aclose()
                session.is_busy = False  # Explicitly clear just in case
                # Send result back to Claude
                return await self._complete_code_execution(
                    result,
                    execute_code_call,
                    claude_response,
                    original_request,
                    bedrock_service,
                    request_id,
                    service_tier,
                    session,
                    ptc_callable_tools
                )

        except StopAsyncIteration:
            # Generator completed without yielding
            logger.warning("Sandbox generator completed unexpectedly")
            raise SandboxError("Code execution completed unexpectedly")

    async def resume_execution(
        self,
        session_id: str,
        tool_result: Any,
        is_error: bool = False
    ) -> Tuple[Any, bool]:
        """
        Resume code execution after tool result.

        Args:
            session_id: Session ID
            tool_result: Result from tool execution (or error message)
            is_error: Whether the result is an error

        Returns:
            Tuple of (next_result, is_complete)
            - next_result: Either ToolCallRequest or ExecutionResult
            - is_complete: True if execution is complete
        """
        state = self._execution_states.get(session_id)
        gen = self._execution_generators.get(session_id)

        if not state or not gen:
            raise ValueError(f"No pending execution for session {session_id}")

        try:
            if is_error:
                # Inject error into sandbox
                session = self.sandbox_executor.get_session(session_id)
                if session:
                    self.sandbox_executor.inject_tool_error(
                        session,
                        state.pending_tool_call_id,
                        str(tool_result)
                    )
                # Get next result
                result = await gen.__anext__()
            else:
                # Send result and get next
                result = await gen.asend(tool_result)

            if isinstance(result, ToolCallRequest):
                # Another single tool call
                state.pending_tool_call_id = result.call_id
                state.pending_tool_name = result.tool_name
                state.pending_tool_input = result.arguments
                state.pending_batch_call_ids = None  # Clear batch IDs
                state.tool_call_count += 1
                return result, False
            elif isinstance(result, BatchToolCallRequest):
                # Batch of parallel tool calls
                first_call = result.requests[0]
                state.pending_tool_call_id = first_call.call_id
                state.pending_tool_name = first_call.tool_name
                state.pending_tool_input = first_call.arguments
                state.pending_batch_call_ids = [r.call_id for r in result.requests]
                state.tool_call_count += len(result.requests)
                return result, False
            else:
                # Execution complete
                self._cleanup_execution_state(session_id)
                return result, True

        except StopAsyncIteration:
            self._cleanup_execution_state(session_id)
            return None, True

    async def handle_tool_result_continuation(
        self,
        session_id: str,
        tool_result: Any,
        is_error: bool,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Handle tool_result continuation for a pending sandbox execution.

        This is called when client sends back a tool_result for a PTC-originated
        tool call. It resumes the paused sandbox execution.

        Args:
            session_id: The session/container ID
            tool_result: The result from the client's tool execution
            is_error: Whether the tool execution resulted in an error
            original_request: The original request (for context)
            bedrock_service: Bedrock service for Claude calls
            request_id: Request ID for logging
            service_tier: Service tier for Bedrock

        Returns:
            Tuple of (response, container_info)
        """
        state = self._execution_states.get(session_id)
        if not state:
            raise ValueError(f"No pending execution for session {session_id}")

        logger.info(f"[PTC] Resuming execution for session {session_id}, tool={state.pending_tool_name}")

        # Get PTC tools for potential continuation
        _, ptc_callable_tools = self.get_ptc_tools(original_request)

        # Resume sandbox execution
        result, is_complete = await self.resume_execution(session_id, tool_result, is_error)

        session = self.sandbox_executor.get_session(session_id)
        if not session:
            raise SandboxError(f"Session {session_id} not found")

        if not is_complete and isinstance(result, (ToolCallRequest, BatchToolCallRequest)):
            # Tool call(s) - return to client
            container_info = ContainerInfo(
                id=session_id,
                expires_at=session.expires_at.isoformat()
            )

            if isinstance(result, BatchToolCallRequest):
                # Multiple parallel tool calls
                logger.info(f"[PTC] Continuation yielded batch of {len(result)} tool calls")
                first_call = result.requests[0]
                pending_call_ids = [r.call_id for r in result.requests]

                # Update state for batch
                state.pending_batch_call_ids = pending_call_ids
                state.pending_tool_call_id = first_call.call_id
                state.pending_tool_name = first_call.tool_name
                state.pending_tool_input = first_call.arguments
                self._execution_states[session_id] = state

                # Update session's pending tool call
                session.pending_tool_call = PendingToolCall(
                    call_id=first_call.call_id,
                    tool_name=first_call.tool_name,
                    arguments=first_call.arguments,
                    session_id=session_id,
                    code_execution_tool_id=state.code_execution_tool_id
                )

                # Build minimal response with multiple tool_use blocks
                response = self._build_batch_tool_use_response_minimal(
                    result,
                    state.code_execution_tool_id,
                    container_info,
                    model=original_request.model,
                    code=state.code
                )

                return response, container_info

            else:
                # Single tool call
                # Update session's pending tool call
                session.pending_tool_call = PendingToolCall(
                    call_id=result.call_id,
                    tool_name=result.tool_name,
                    arguments=result.arguments,
                    session_id=session_id,
                    code_execution_tool_id=state.code_execution_tool_id
                )

                # Clear batch call IDs since this is single
                state.pending_batch_call_ids = None
                self._execution_states[session_id] = state

                # Build minimal response with tool_use
                response = self._build_tool_use_response_minimal(
                    result,
                    state.code_execution_tool_id,
                    container_info,
                    model=original_request.model,
                    code=state.code
                )

                return response, container_info

        elif is_complete and isinstance(result, ExecutionResult):
            # Execution complete - call Claude to get final response
            logger.info(f"[PTC] Sandbox execution completed: success={result.success}")

            container_info = ContainerInfo(
                id=session_id,
                expires_at=session.expires_at.isoformat()
            )

            # Call Claude with the code execution result to get final response
            return await self._finalize_code_execution(
                result=result,
                code_execution_tool_id=state.code_execution_tool_id,
                original_request=original_request,
                bedrock_service=bedrock_service,
                request_id=request_id,
                service_tier=service_tier,
                session=session,
                ptc_callable_tools=ptc_callable_tools,
                code=state.code
            )

        else:
            # Unexpected state
            self._cleanup_execution_state(session_id)
            raise SandboxError(f"Unexpected result type: {type(result)}")

    def _build_code_execution_complete_response(
        self,
        result: ExecutionResult,
        code_execution_tool_id: str,
        model: str,
        code: str = ""
    ) -> MessageResponse:
        """Build response when code execution completes."""
        from app.schemas.anthropic import Usage

        # Build content with server_tool_use and server_tool_result blocks
        content = [
            # Server tool use block (code_execution)
            {
                "type": "server_tool_use",
                "id": code_execution_tool_id,
                "name": "code_execution",
                "input": {"code": code}  # Include actual code for client visibility
            },
            # Server tool result block (code execution output)
            {
                "type": "server_tool_result",
                "tool_use_id": code_execution_tool_id,
                "content": [
                    {
                        "type": "code_execution_result",
                        "stdout": result.stdout or "",
                        "stderr": result.stderr or "",
                        "return_code": 0 if result.success else 1
                    }
                ]
            }
        ]

        return MessageResponse(
            id=f"msg_{uuid4().hex}",
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=0, output_tokens=0)  # Continuation has minimal tokens
        )

    async def _finalize_code_execution(
        self,
        result: ExecutionResult,
        code_execution_tool_id: str,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        session: SandboxSession,
        ptc_callable_tools: List[dict],
        code: str = "" 
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Finalize code execution by calling Claude with the result.

        This is called after sandbox code completes (in continuation flow).
        It sends the code output to Claude and returns Claude's final response.
        """
        # Build tool result content
        if result.success:
            tool_result_content = result.stdout or "(Code executed successfully with no output)"
        else:
            tool_result_content = f"Error: {result.stderr}"

        logger.info(f"[PTC] Finalizing code execution, sending result to Claude: {tool_result_content[:200]}...")

        # Build continuation messages
        # The original_request.messages should contain the conversation history
        # We need to add an assistant message with execute_code tool_use
        # and a user message with the tool_result
        # Filter out non-direct tool calls and their results from history
        messages = _filter_non_direct_tool_calls(list(original_request.messages))

        # Add assistant message with execute_code tool call
        # (This is what Claude would have sent that triggered the code execution)
        messages.append({
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": f"toolu_{code_execution_tool_id[-12:]}",  # Use a derived ID
                "name": "execute_code",
                "input": {"code": code}  # Don't need actual code
            }]
        })

        # Add tool result for the execute_code call
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": f"toolu_{code_execution_tool_id[-12:]}",
                "content": tool_result_content
            }]
        })

        # Create continuation request
        continuation_request = MessageRequest(
            model=original_request.model,
            messages=messages,
            max_tokens=original_request.max_tokens,
            system=original_request.system,
            temperature=original_request.temperature,
            top_p=original_request.top_p,
            top_k=original_request.top_k,
            stop_sequences=original_request.stop_sequences,
            tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
            tool_choice=original_request.tool_choice,
            thinking=original_request.thinking,
        )

        # Call Bedrock to get Claude's final response
        final_response = await bedrock_service.invoke_model(
            continuation_request, request_id, service_tier
        )

        # Check if Claude called execute_code again
        next_execute_code = self._find_execute_code_call(final_response)

        if next_execute_code:
            # Recursive call for multi-round code execution
            return await self._handle_code_execution(
                next_execute_code,
                final_response,
                session,
                MessageRequest(**{**original_request.model_dump(), "messages": messages}),
                bedrock_service,
                request_id,
                service_tier,
                ptc_callable_tools
            )

        # Add caller: {type: "direct"} to any direct tool_use blocks
        final_response = self._add_direct_caller_to_tool_use(final_response)

        container_info = ContainerInfo(
            id=session.session_id,
            expires_at=session.expires_at.isoformat()
        )

        return final_response, container_info

    def _build_tool_use_response_minimal(
        self,
        tool_request: ToolCallRequest,
        code_execution_tool_id: str,
        _container_info: ContainerInfo,  # Unused, kept for future use
        model: str = "claude-3-sonnet",
        code: str = ""  # Unused in minimal response
    ) -> MessageResponse:
        """Build minimal response with tool_use for continuation.

        NOTE: This does NOT include server_tool_use because it's a continuation.
        The server_tool_use was already sent in the initial response.
        Continuation responses only include new tool_use blocks.
        """
        from app.schemas.anthropic import Usage

        # Only include tool_use block - server_tool_use was already sent in initial response
        content = [
            {
                "type": "tool_use",
                "id": f"toolu_{uuid4().hex[:12]}",
                "name": tool_request.tool_name,
                "input": tool_request.arguments,
                "caller": {
                    "type": PTC_ALLOWED_CALLER,
                    "tool_id": code_execution_tool_id
                }
            }
        ]

        return MessageResponse(
            id=f"msg_{uuid4().hex}",
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason="tool_use",
            stop_sequence=None,
            usage=Usage(input_tokens=0, output_tokens=0)  # Continuation has no new tokens
        )

    def _build_batch_tool_use_response_minimal(
        self,
        batch_request: BatchToolCallRequest,
        code_execution_tool_id: str,
        _container_info: ContainerInfo,  # Unused, kept for future use
        model: str = "claude-3-sonnet",
        code: str = ""  # Unused in minimal response
    ) -> MessageResponse:
        """Build minimal response with multiple tool_use blocks for batch continuation.

        NOTE: This does NOT include server_tool_use because it's a continuation.
        The server_tool_use was already sent in the initial response.
        Continuation responses only include new tool_use blocks.
        """
        from app.schemas.anthropic import Usage

        # Only include tool_use blocks - server_tool_use was already sent in initial response
        content = []

        # Add tool use block for EACH tool call in the batch
        for tool_request in batch_request.requests:
            content.append({
                "type": "tool_use",
                "id": f"toolu_{tool_request.call_id[:12]}",  # Use call_id for tracking
                "name": tool_request.tool_name,
                "input": tool_request.arguments,
                "caller": {
                    "type": PTC_ALLOWED_CALLER,
                    "tool_id": code_execution_tool_id
                }
            })

        logger.info(f"[PTC] Built batch minimal response with {len(batch_request)} tool calls (continuation, no server_tool_use)")

        return MessageResponse(
            id=f"msg_{uuid4().hex}",
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason="tool_use",
            stop_sequence=None,
            usage=Usage(input_tokens=0, output_tokens=0)  # Continuation has no new tokens
        )

    async def _continue_after_code_execution(
        self,
        result: ExecutionResult,
        code_execution_tool_id: str,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        session: SandboxSession,
        ptc_callable_tools: List[dict]
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """Continue conversation with Claude after code execution completes."""
        # Build tool result content
        if result.success:
            tool_result_content = result.stdout or "(Code executed successfully with no output)"
        else:
            tool_result_content = f"Error: {result.stderr}"

        # Build continuation messages
        # Filter out non-direct tool calls and their results from history
        messages = _filter_non_direct_tool_calls(list(original_request.messages))

        # Add tool result for the server_tool_use (code_execution)
        # Find the last assistant message with server_tool_use and add result
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": code_execution_tool_id,
                "content": tool_result_content
            }]
        })

        # Create continuation request
        continuation_request = MessageRequest(
            model=original_request.model,
            messages=messages,
            max_tokens=original_request.max_tokens,
            system=original_request.system,
            temperature=original_request.temperature,
            top_p=original_request.top_p,
            top_k=original_request.top_k,
            stop_sequences=original_request.stop_sequences,
            tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
            tool_choice=original_request.tool_choice,
            thinking=original_request.thinking,
        )

        # Call Bedrock again
        final_response = await bedrock_service.invoke_model(
            continuation_request, request_id, service_tier
        )

        # Check if Claude called execute_code again
        next_execute_code = self._find_execute_code_call(final_response)

        if next_execute_code:
            # Recursive call for multi-round code execution
            return await self._handle_code_execution(
                next_execute_code,
                final_response,
                session,
                MessageRequest(**{**original_request.model_dump(), "messages": messages}),
                bedrock_service,
                request_id,
                service_tier,
                ptc_callable_tools
            )

        # Add caller: {type: "direct"} to any direct tool_use blocks
        final_response = self._add_direct_caller_to_tool_use(final_response)

        container_info = ContainerInfo(
            id=session.session_id,
            expires_at=session.expires_at.isoformat()
        )

        return final_response, container_info

    def _cleanup_execution_state(self, session_id: str) -> None:
        """Clean up execution state."""
        self._execution_states.pop(session_id, None)
        self._execution_generators.pop(session_id, None)
        # Also clear session's pending_tool_call if session exists
        session = self.sandbox_executor.get_session(session_id)
        if session:
            session.pending_tool_call = None
            session.is_busy = False

    def _build_tool_use_response(
        self,
        tool_request: ToolCallRequest,
        code_execution_tool_id: str,
        original_response: MessageResponse,
        _container_info: ContainerInfo,  # Unused, kept for future use
        code: str = ""
    ) -> MessageResponse:
        """Build response with tool_use block including caller info."""
        # Create new content with tool_use
        content = []

        # Add any text content from original response
        for block in original_response.content:
            if hasattr(block, "type"):
                if block.type == "text":
                    content.append({
                        "type": "text",
                        "text": block.text if hasattr(block, "text") else ""
                    })
            elif isinstance(block, dict) and block.get("type") == "text":
                content.append(block)

        # Add server_tool_use for code_execution
        content.append({
            "type": "server_tool_use",
            "id": code_execution_tool_id,
            "name": "code_execution",
            "input": {"code": code}  # Include actual code for client visibility
        })

        # Add tool_use with caller info
        content.append({
            "type": "tool_use",
            "id": f"toolu_{uuid4().hex[:12]}",
            "name": tool_request.tool_name,
            "input": tool_request.arguments,
            "caller": {
                "type": PTC_ALLOWED_CALLER,
                "tool_id": code_execution_tool_id
            }
        })

        # Build response
        response_dict = {
            "id": original_response.id,
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": original_response.model,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": original_response.usage.model_dump() if hasattr(original_response.usage, "model_dump") else original_response.usage,
        }

        logger.debug(f"[PTC] Built tool_use response content: {json.dumps(content, indent=2)}")
        return MessageResponse(**response_dict)

    def _build_batch_tool_use_response(
        self,
        batch_request: BatchToolCallRequest,
        code_execution_tool_id: str,
        original_response: MessageResponse,
        _container_info: ContainerInfo,  # Unused, kept for future use
        code: str = ""
    ) -> MessageResponse:
        """Build response with multiple tool_use blocks for parallel tool calls."""
        # Create new content
        content = []

        # Add any text content from original response
        for block in original_response.content:
            if hasattr(block, "type"):
                if block.type == "text":
                    content.append({
                        "type": "text",
                        "text": block.text if hasattr(block, "text") else ""
                    })
            elif isinstance(block, dict) and block.get("type") == "text":
                content.append(block)

        # Add server_tool_use for code_execution
        content.append({
            "type": "server_tool_use",
            "id": code_execution_tool_id,
            "name": "code_execution",
            "input": {"code": code}  # Include actual code for client visibility
        })

        # Add tool_use block for EACH tool call in the batch
        for tool_request in batch_request.requests:
            content.append({
                "type": "tool_use",
                "id": f"toolu_{tool_request.call_id[:12]}",  # Use call_id for tracking
                "name": tool_request.tool_name,
                "input": tool_request.arguments,
                "caller": {
                    "type": PTC_ALLOWED_CALLER,
                    "tool_id": code_execution_tool_id
                }
            })

        # Build response
        response_dict = {
            "id": original_response.id,
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": original_response.model,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": original_response.usage.model_dump() if hasattr(original_response.usage, "model_dump") else original_response.usage,
        }

        logger.info(f"[PTC] Built batch tool_use response with {len(batch_request)} tool calls")
        return MessageResponse(**response_dict)

    async def _complete_code_execution(
        self,
        result: ExecutionResult,
        execute_code_call: dict,
        claude_response: MessageResponse,
        original_request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        session: SandboxSession,
        ptc_callable_tools: List[dict]
    ) -> Tuple[MessageResponse, Optional[ContainerInfo]]:
        """
        Complete code execution and continue conversation with Claude.

        After code execution completes, send the result back to Claude
        as a tool_result and get the final response.
        """
        # Build tool result content
        if result.success:
            tool_result_content = result.stdout or "(Code executed successfully with no output)"
        else:
            tool_result_content = f"Error: {result.stderr}"

        # Build continuation messages
        # Include original assistant response and tool result
        # Filter out non-direct tool calls and their results from history
        messages = _filter_non_direct_tool_calls(list(original_request.messages))

        # Add assistant message with execute_code call
        assistant_content = []
        for block in claude_response.content:
            if hasattr(block, "model_dump"):
                assistant_content.append(block.model_dump())
            elif isinstance(block, dict):
                assistant_content.append(block)

        messages.append({
            "role": "assistant",
            "content": assistant_content
        })

        # Add tool result
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": execute_code_call["id"],
                "content": tool_result_content
            }]
        })

        # Create continuation request
        continuation_request = MessageRequest(
            model=original_request.model,
            messages=messages,
            max_tokens=original_request.max_tokens,
            system=original_request.system,
            temperature=original_request.temperature,
            top_p=original_request.top_p,
            top_k=original_request.top_k,
            stop_sequences=original_request.stop_sequences,
            tools=self.prepare_bedrock_request(original_request, ptc_callable_tools).tools,
            tool_choice=original_request.tool_choice,
            thinking=original_request.thinking,
        )

        # Call Bedrock again
        final_response = await bedrock_service.invoke_model(
            continuation_request, request_id, service_tier
        )

        # Check if Claude called execute_code again
        next_execute_code = self._find_execute_code_call(final_response)

        if next_execute_code:
            # Recursive call for multi-round code execution
            return await self._handle_code_execution(
                next_execute_code,
                final_response,
                session,
                MessageRequest(**{**original_request.model_dump(), "messages": messages}),
                bedrock_service,
                request_id,
                service_tier,
                ptc_callable_tools
            )

        # Add caller: {type: "direct"} to any direct tool_use blocks
        final_response = self._add_direct_caller_to_tool_use(final_response)

        container_info = ContainerInfo(
            id=session.session_id,
            expires_at=session.expires_at.isoformat()
        )

        return final_response, container_info

    def _add_direct_caller_to_tool_use(self, response: MessageResponse) -> MessageResponse:
        """
        Add caller: {type: "direct"} to any tool_use blocks without a caller.

        When PTC is enabled, all tool_use blocks should have a caller field.
        Direct tool calls (not from code execution) get caller.type = "direct".
        """
        new_content = []
        modified = False

        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                # Check if already has caller
                if not hasattr(block, "caller") or block.caller is None:
                    # Convert to dict, add caller, and append
                    block_dict = block.model_dump() if hasattr(block, "model_dump") else dict(block)
                    block_dict["caller"] = {"type": "direct"}
                    new_content.append(block_dict)
                    modified = True
                else:
                    new_content.append(block.model_dump() if hasattr(block, "model_dump") else block)
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                if "caller" not in block or block.get("caller") is None:
                    block_copy = dict(block)
                    block_copy["caller"] = {"type": "direct"}
                    new_content.append(block_copy)
                    modified = True
                else:
                    new_content.append(block)
            else:
                # Keep other content blocks as-is
                if hasattr(block, "model_dump"):
                    new_content.append(block.model_dump())
                else:
                    new_content.append(block)

        if modified:
            response_dict = response.model_dump()
            response_dict["content"] = new_content
            return MessageResponse(**response_dict)

        return response

    def get_pending_execution(self, session_id: str) -> Optional[PTCExecutionState]:
        """Get pending execution state for a session."""
        return self._execution_states.get(session_id)

    async def shutdown(self) -> None:
        """Shutdown PTC service and cleanup resources."""
        if self._sandbox_executor:
            self._sandbox_executor.stop_cleanup_task()
            await self._sandbox_executor.close_all_sessions()


# Global PTC service instance
_ptc_service: Optional[PTCService] = None


def get_ptc_service() -> PTCService:
    """Get global PTC service instance."""
    global _ptc_service
    if _ptc_service is None:
        _ptc_service = PTCService()
    return _ptc_service
