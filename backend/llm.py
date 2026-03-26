import asyncio
import os
import httpx

# Primary: Databricks-hosted Claude (works locally, not from Railway)
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
DATABRICKS_HOST = os.getenv(
    "DATABRICKS_HOST",
    "https://8259562368007470.0.gcp.databricks.com"
)
DATABRICKS_MODEL = os.getenv("DATABRICKS_MODEL", "databricks-claude-sonnet-4-6")

# Fallback: OpenRouter (free tier)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

# Local fallback: Ollama
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")


async def complete(prompt: str, system: str = "", max_tokens: int = 1024, retries: int = 2) -> str:
    """Try each provider in order, with retries on transient failures."""
    providers = []
    if DATABRICKS_TOKEN:
        providers.append(("Databricks", _databricks_complete))
    if OPENROUTER_API_KEY:
        providers.append(("OpenRouter", _openrouter_complete))
    providers.append(("Ollama", _ollama_complete))

    last_error = None
    for name, fn in providers:
        for attempt in range(1, retries + 1):
            try:
                result = await fn(prompt, system, max_tokens)
                if result and result.strip():
                    return result
                print(f"[llm] {name} returned empty (attempt {attempt}/{retries})")
            except Exception as e:
                last_error = e
                print(f"[llm] {name} failed attempt {attempt}/{retries}: {e}")
                if attempt < retries:
                    await asyncio.sleep(2 * attempt)  # simple backoff

    raise last_error or RuntimeError("All LLM providers failed")


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


async def _openrouter_complete(prompt: str, system: str, max_tokens: int) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://well.un-dios.com",
                "X-Title": "The Well",
            },
            json={
                "model": OPENROUTER_MODEL,
                "max_tokens": max_tokens,
                "messages": messages,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        # Some free models are "thinking" models that put output in reasoning when content is null
        return msg.get("content") or msg.get("reasoning") or ""


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
