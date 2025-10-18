#!/usr/bin/env python3
"""
Script to create API keys in DynamoDB.

Usage:
    python scripts/create_api_key.py --user-id <user_id> --name <key_name>
"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.dynamodb import APIKeyManager, DynamoDBClient


def main():
    """Create API key."""
    parser = argparse.ArgumentParser(description="Create API key")
    parser.add_argument(
        "--user-id",
        required=True,
        help="User ID for the API key",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Name/description for the API key",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=None,
        help="Custom rate limit (requests per window)",
    )

    args = parser.parse_args()

    # Initialize DynamoDB client
    print("Initializing DynamoDB client...")
    dynamodb_client = DynamoDBClient()

    # Create API key manager
    api_key_manager = APIKeyManager(dynamodb_client)

    # Create API key
    print(f"Creating API key for user: {args.user_id}")
    api_key = api_key_manager.create_api_key(
        user_id=args.user_id,
        name=args.name,
        rate_limit=args.rate_limit,
    )

    print(f"\nAPI Key created successfully!")
    print(f"API Key: {api_key}")
    print(f"User ID: {args.user_id}")
    print(f"Name: {args.name}")
    if args.rate_limit:
        print(f"Rate Limit: {args.rate_limit} requests per window")

    print(f"\nUse this key in requests:")
    print(f"  x-api-key: {api_key}")


if __name__ == "__main__":
    main()
