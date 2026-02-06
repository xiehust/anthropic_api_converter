import json
import time
from typing import Dict, List, Optional, Any
from anthropic import Anthropic


# Import test configuration
from config import API_KEY, BASE_URL


# ============================================================
# Configuration
# ============================================================

# Initialize client
client = Anthropic(api_key=API_KEY, base_url=BASE_URL)


response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={
        "effort": "high"
    },
    messages=[{"role": "user", "content": "explain how transformers work"}]
)

print(response)