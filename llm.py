"""Minimal client for the Meta Model API (OpenAI-compatible chat completions)."""

import json
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("META_API_BASE", "https://api.meta.ai/v1")
DEFAULT_MODEL = os.getenv("META_MODEL", "muse-spark-1.3")


class LLMError(RuntimeError):
    pass


def chat(messages, model=None, temperature=0.7, timeout=120):
    """Send a chat request and return the reply text."""
    key = os.getenv("META_API_KEY", "")
    if not key or key == "your_key_here":
        raise LLMError("META_API_KEY is not set. Put your key in the .env file.")

    resp = requests.post(
        f"{API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model or DEFAULT_MODEL, "messages": messages, "temperature": temperature},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise LLMError(f"Meta API error {resp.status_code}: {resp.text[:500]}")
    return resp.json()["choices"][0]["message"]["content"]


def extract_json(text):
    """Pull a JSON value out of a model reply, tolerating ```json fences or extra prose."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        raise LLMError(f"No JSON found in reply: {text[:300]}")
    return json.loads(text[start:])
