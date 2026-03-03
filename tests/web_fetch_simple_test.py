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


response = client.beta.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[
        {
            "role": "user",
            "content": "Fetch the content at https://github.com/anthropics/anthropic-sdk-python/issues/1105 and extract the key findings.",
        }
    ],
    tools=[{"type": "web_fetch_20260209",
             "name": "web_fetch",
               "max_uses": 5,
               "citations": {
                    "enabled": True,
                },
                "max_content_tokens": 100000
               }],
    extra_headers={"anthropic-beta": "web-fetch-2025-09-10"},
)
print(response.to_json())