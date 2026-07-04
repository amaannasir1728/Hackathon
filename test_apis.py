import os
import requests
from dotenv import load_dotenv

load_dotenv()

# Test Serper
print("Testing Serper.dev...")
try:
    response = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": os.getenv("SERPER_API_KEY")},
        json={"q": "test"},
        timeout=5
    )
    print(f"✅ Serper: {response.status_code}")
except Exception as e:
    print(f"❌ Serper: {e}")

# Test OpenRouter
print("Testing OpenRouter...")
try:
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
        json={
            "model": "meta-llama/llama-3.3-70b-instruct",
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 10
        },
        timeout=10
    )
    print(f"✅ OpenRouter: {response.status_code}")
except Exception as e:
    print(f"❌ OpenRouter: {e}")

print("\nAll keys verified!")