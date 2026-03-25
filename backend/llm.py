import os
import httpx

# Primary: Databricks-hosted Claude
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
DATABRICKS_HOST = os.getenv(
    "DATABRICKS_HOST",
    "https://8259562368007470.0.gcp.databricks.com"
)
DATABRICKS_MODEL = os.getenv("DATABRICKS_MODEL", "databricks-claude-sonnet-4-6")

# Fallback: Direct Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# Local fallback: Ollama
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")


async def complete(prompt: str, system: str = "", max_tokens: int = 1024) -> str:
    # Try Databricks first
    if DATABRICKS_TOKEN:
        try:
            return await _databricks_complete(prompt, system, max_tokens)
        except Exception as e:
            print(f"[llm] Databricks failed ({e})")

    # Fallback to direct Anthropic API
    if ANTHROPIC_API_KEY:
        try:
            return await _anthropic_complete(prompt, system, max_tokens)
        except Exception as e:
            print(f"[llm] Anthropic API failed ({e})")

    # Last resort: Ollama
    try:
        return await _ollama_complete(prompt, system, max_tokens)
    except Exception as e:
        print(f"[llm] Ollama failed ({e})")
        raise


async def _databricks_complete(prompt: str, system: str, max_tokens: int) -> str:
    messages = [{"role": "user", "content": prompt}]
    payload: dict = {
        "model": DATABRICKS_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{DATABRICKS_HOST}/serving-endpoints/anthropic/v1/messages",
            headers={
                "Authorization": f"Bearer {DATABRICKS_TOKEN}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


async def _anthropic_complete(prompt: str, system: str, max_tokens: int) -> str:
    messages = [{"role": "user", "content": prompt}]
    payload: dict = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


async def _ollama_complete(prompt: str, system: str, max_tokens: int) -> str:
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": full_prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
        )
        resp.raise_for_status()
        return resp.json()["response"]
