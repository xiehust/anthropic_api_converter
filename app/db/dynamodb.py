"""
DynamoDB client and table management.

Provides interfaces for interacting with DynamoDB tables for API keys,
usage tracking, caching, and model mapping.
"""
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings


class DynamoDBClient:
    """DynamoDB client for managing tables and operations."""

    def __init__(self):
        """Initialize DynamoDB client."""
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=settings.aws_region,
            endpoint_url=settings.dynamodb_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_session_token=settings.aws_session_token,
        )

        self.api_keys_table_name = settings.dynamodb_api_keys_table
        self.usage_table_name = settings.dynamodb_usage_table
        self.cache_table_name = settings.dynamodb_cache_table
        self.model_mapping_table_name = settings.dynamodb_model_mapping_table

    def create_tables(self):
        """Create all required DynamoDB tables if they don't exist."""
        self._create_api_keys_table()
        self._create_usage_table()
        self._create_cache_table()
        self._create_model_mapping_table()

    def _create_api_keys_table(self):
        """Create API keys table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.api_keys_table_name,
                KeySchema=[
                    {"AttributeName": "api_key", "KeyType": "HASH"},  # Partition key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "api_key", "AttributeType": "S"},
                    {"AttributeName": "user_id", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "user_id-index",
                        "KeySchema": [
                            {"AttributeName": "user_id", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.api_keys_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.api_keys_table_name}")
            else:
                raise

    def _create_usage_table(self):
        """Create usage tracking table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.usage_table_name,
                KeySchema=[
                    {"AttributeName": "api_key", "KeyType": "HASH"},  # Partition key
                    {"AttributeName": "timestamp", "KeyType": "RANGE"},  # Sort key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "api_key", "AttributeType": "S"},
                    {"AttributeName": "timestamp", "AttributeType": "N"},
                    {"AttributeName": "request_id", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "request_id-index",
                        "KeySchema": [
                            {"AttributeName": "request_id", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.usage_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.usage_table_name}")
            else:
                raise

    def _create_cache_table(self):
        """Create cache table with TTL."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.cache_table_name,
                KeySchema=[
                    {"AttributeName": "cache_key", "KeyType": "HASH"},  # Partition key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "cache_key", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()

            # Enable TTL
            client = boto3.client(
                "dynamodb",
                region_name=settings.aws_region,
                endpoint_url=settings.dynamodb_endpoint_url,
            )
            client.update_time_to_live(
                TableName=self.cache_table_name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
            )

            print(f"Created table with TTL: {self.cache_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.cache_table_name}")
            else:
                raise

    def _create_model_mapping_table(self):
        """Create model mapping table."""
        try:
            table = self.dynamodb.create_table(
                TableName=self.model_mapping_table_name,
                KeySchema=[
                    {
                        "AttributeName": "anthropic_model_id",
                        "KeyType": "HASH",
                    },  # Partition key
                ],
                AttributeDefinitions=[
                    {"AttributeName": "anthropic_model_id", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            print(f"Created table: {self.model_mapping_table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                print(f"Table already exists: {self.model_mapping_table_name}")
            else:
                raise


class APIKeyManager:
    """Manager for API key operations."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize API key manager."""
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.api_keys_table_name)

    def create_api_key(
        self,
        user_id: str,
        name: str,
        rate_limit: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a new API key.

        Args:
            user_id: User identifier
            name: Human-readable name for the key
            rate_limit: Optional custom rate limit
            metadata: Optional metadata dictionary

        Returns:
            Generated API key
        """
        api_key = f"sk-{uuid4().hex}"
        timestamp = int(time.time())

        item = {
            "api_key": api_key,
            "user_id": user_id,
            "name": name,
            "created_at": timestamp,
            "is_active": True,
            "rate_limit": rate_limit or settings.rate_limit_requests,
            "metadata": metadata or {},
        }

        self.table.put_item(Item=item)
        return api_key

    def validate_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Validate an API key and return its details.

        Args:
            api_key: API key to validate

        Returns:
            API key details if valid, None otherwise
        """
        try:
            response = self.table.get_item(Key={"api_key": api_key})
            item = response.get("Item")

            if item and item.get("is_active", False):
                return item

            return None
        except ClientError:
            return None

    def deactivate_api_key(self, api_key: str):
        """
        Deactivate an API key.

        Args:
            api_key: API key to deactivate
        """
        self.table.update_item(
            Key={"api_key": api_key},
            UpdateExpression="SET is_active = :val",
            ExpressionAttributeValues={":val": False},
        )

    def list_api_keys_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """
        List all API keys for a user.

        Args:
            user_id: User identifier

        Returns:
            List of API key details
        """
        response = self.table.query(
            IndexName="user_id-index",
            KeyConditionExpression="user_id = :user_id",
            ExpressionAttributeValues={":user_id": user_id},
        )
        return response.get("Items", [])


class UsageTracker:
    """Tracker for API usage and analytics."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize usage tracker."""
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.usage_table_name)

    def record_usage(
        self,
        api_key: str,
        request_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Record API usage.

        Args:
            api_key: API key used
            request_id: Request identifier
            model: Model used
            input_tokens: Input token count
            output_tokens: Output token count
            cached_tokens: Cached token count
            success: Whether request was successful
            error_message: Error message if failed
            metadata: Optional metadata
        """
        timestamp = int(time.time() * 1000)  # milliseconds

        item = {
            "api_key": api_key,
            "timestamp": timestamp,
            "request_id": request_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": input_tokens + output_tokens,
            "success": success,
            "error_message": error_message,
            "metadata": metadata or {},
        }

        self.table.put_item(Item=item)

    def get_usage_stats(
        self, api_key: str, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get usage statistics for an API key.

        Args:
            api_key: API key to query
            start_time: Start of time range
            end_time: End of time range

        Returns:
            Usage statistics dictionary
        """
        if not start_time:
            start_time = datetime.now() - timedelta(days=30)
        if not end_time:
            end_time = datetime.now()

        start_timestamp = int(start_time.timestamp() * 1000)
        end_timestamp = int(end_time.timestamp() * 1000)

        response = self.table.query(
            KeyConditionExpression="api_key = :api_key AND #ts BETWEEN :start AND :end",
            ExpressionAttributeNames={"#ts": "timestamp"},
            ExpressionAttributeValues={
                ":api_key": api_key,
                ":start": start_timestamp,
                ":end": end_timestamp,
            },
        )

        items = response.get("Items", [])

        # Aggregate statistics
        total_requests = len(items)
        total_input_tokens = sum(item.get("input_tokens", 0) for item in items)
        total_output_tokens = sum(item.get("output_tokens", 0) for item in items)
        total_cached_tokens = sum(item.get("cached_tokens", 0) for item in items)
        successful_requests = sum(1 for item in items if item.get("success", False))

        return {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": total_requests - successful_requests,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cached_tokens": total_cached_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
        }


class CacheManager:
    """Manager for response caching."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize cache manager."""
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.cache_table_name)

    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        Get cached response.

        Args:
            cache_key: Cache key

        Returns:
            Cached response or None
        """
        try:
            response = self.table.get_item(Key={"cache_key": cache_key})
            item = response.get("Item")

            if item:
                # Check if expired (TTL might not have cleaned up yet)
                if item.get("ttl", 0) > int(time.time()):
                    return item.get("response")

            return None
        except ClientError:
            return None

    def set(self, cache_key: str, response: Dict[str, Any], ttl: Optional[int] = None):
        """
        Cache a response.

        Args:
            cache_key: Cache key
            response: Response to cache
            ttl: Time to live in seconds
        """
        if not ttl:
            ttl = settings.cache_ttl

        expiration = int(time.time()) + ttl

        item = {
            "cache_key": cache_key,
            "response": response,
            "ttl": expiration,
            "created_at": int(time.time()),
        }

        self.table.put_item(Item=item)

    def delete(self, cache_key: str):
        """
        Delete cached response.

        Args:
            cache_key: Cache key
        """
        self.table.delete_item(Key={"cache_key": cache_key})


class ModelMappingManager:
    """Manager for custom model mappings."""

    def __init__(self, dynamodb_client: DynamoDBClient):
        """Initialize model mapping manager."""
        self.dynamodb = dynamodb_client.dynamodb
        self.table = self.dynamodb.Table(dynamodb_client.model_mapping_table_name)

    def get_mapping(self, anthropic_model_id: str) -> Optional[str]:
        """
        Get Bedrock model ID for an Anthropic model ID.

        Args:
            anthropic_model_id: Anthropic model identifier

        Returns:
            Bedrock model ARN or None
        """
        try:
            response = self.table.get_item(
                Key={"anthropic_model_id": anthropic_model_id}
            )
            item = response.get("Item")
            return item.get("bedrock_model_id") if item else None
        except ClientError:
            return None

    def set_mapping(self, anthropic_model_id: str, bedrock_model_id: str):
        """
        Set custom model mapping.

        Args:
            anthropic_model_id: Anthropic model identifier
            bedrock_model_id: Bedrock model ARN
        """
        item = {
            "anthropic_model_id": anthropic_model_id,
            "bedrock_model_id": bedrock_model_id,
            "updated_at": int(time.time()),
        }
        self.table.put_item(Item=item)

    def delete_mapping(self, anthropic_model_id: str):
        """
        Delete custom model mapping.

        Args:
            anthropic_model_id: Anthropic model identifier
        """
        self.table.delete_item(Key={"anthropic_model_id": anthropic_model_id})

    def list_mappings(self) -> List[Dict[str, str]]:
        """
        List all custom model mappings.

        Returns:
            List of model mappings
        """
        response = self.table.scan()
        return response.get("Items", [])
