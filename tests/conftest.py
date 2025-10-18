"""
Pytest configuration and fixtures.

Provides shared fixtures for testing.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Authentication headers with master API key."""
    return {settings.api_key_header: settings.master_api_key}


@pytest.fixture
def sample_message_request():
    """Sample Anthropic message request."""
    return {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": "Hello, Claude! How are you?",
            }
        ],
    }


@pytest.fixture
def sample_streaming_request():
    """Sample streaming message request."""
    return {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "stream": True,
        "messages": [
            {
                "role": "user",
                "content": "Tell me a short story.",
            }
        ],
    }


@pytest.fixture
def sample_tool_use_request():
    """Sample message request with tool use."""
    return {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "tools": [
            {
                "name": "get_weather",
                "description": "Get the weather for a location",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City name",
                        },
                    },
                    "required": ["location"],
                },
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": "What's the weather in San Francisco?",
            }
        ],
    }
