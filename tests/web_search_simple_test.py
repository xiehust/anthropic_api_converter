import argparse
import json
import sys

from anthropic import Anthropic

# Import test configuration
from config import API_KEY, BASE_URL, MODEL_ID,ANTHROPIC_API_KEY
if BASE_URL:
    print("=======use api proxy==========")
    client = Anthropic(api_key=API_KEY, base_url=BASE_URL)
else:
    print("=======use official anthropic==========")
    client = Anthropic(api_key=ANTHROPIC_API_KEY)


response = client.messages.create(
    model=MODEL_ID,
    max_tokens=4096,
    messages=[
        {
            "role": "user",
            "content": "Search for the current prices of AAPL and GOOGL, then calculate which has a better P/E ratio.",
        }
    ],
    # tools=[{"type": "web_search_20260209", "name": "web_search"}],
    tools=[{"type": "web_search_20250305", "name": "web_search"}],
)
print(response.to_json())