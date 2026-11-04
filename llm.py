"""Minimal client for OpenAI-compatible chat APIs: the Meta Model API, and Meta's open-weight models
served by inference partners (Baseten, Modal, or anything OpenAI-compatible).

Each agent can use a different provider, set in .env:

    SABOTEUR_PROVIDER=baseten     # run the Saboteur on Muse Spark Open via Baseten
    FIXER_PROVIDER=meta           # keep the Fixer on the Meta Model API

Providers:
    meta      META_API_KEY        (META_API_BASE, META_MODEL optional)
    baseten   BASETEN_API_KEY     (BASETEN_API_BASE, BASETEN_MODEL optional)
    custom    OPEN_API_BASE + OPEN_API_KEY + OPEN_MODEL   (e.g. a Modal endpoint or your own server)
"""

import json
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def _providers():
    return {
        "meta": {
            "label": "Meta Model API",
            "base": os.getenv("META_API_BASE", "https://api.meta.ai/v1"),
            "key": os.getenv("META_API_KEY", ""), "key_name": "META_API_KEY",
            "model": os.getenv("META_MODEL", "muse-spark-1.3"),
        },
        "baseten": {
            "label": "Baseten (open weights)",
            "base": os.getenv("BASETEN_API_BASE", "https://inference.baseten.co/v1"),
            "key": os.getenv("BASETEN_API_KEY", ""), "key_name": "BASETEN_API_KEY",
            "model": os.getenv("BASETEN_MODEL", "meta-models/muse-spark-open-1026"),
        },
        "custom": {
            "label": "Custom OpenAI-compatible endpoint",
            "base": os.getenv("OPEN_API_BASE", ""),
            "key": os.getenv("OPEN_API_KEY", ""), "key_name": "OPEN_API_KEY",
            "model": os.getenv("OPEN_MODEL", ""),
        },
    }


def provider_for(agent, api_key=None):
    name = os.getenv(f"{agent.upper()}_PROVIDER", "meta").strip().lower()
    providers = _providers()
    if name not in providers:
        raise LLMError(f"Unknown {agent.upper()}_PROVIDER '{name}'. Use one of: {', '.join(providers)}.")
    provider = dict(providers[name])
    if api_key:
        provider["key"] = api_key  # explicit key (e.g. a hosted visitor's) wins over the environment
    return name, provider


def model_for(agent):
    """Model name an agent will use, for reports (e.g. 'muse-spark-1.3')."""
    try:
        return provider_for(agent)[1]["model"]
    except LLMError:
        return None


# Kept for older imports: the default model name shown in reports.
DEFAULT_MODEL = os.getenv("META_MODEL", "muse-spark-1.3")


class LLMError(RuntimeError):
    pass


def chat(messages, agent="saboteur", model=None, temperature=0.7, timeout=180, api_key=None):
    """Send a chat request for one agent ("saboteur" or "fixer") and return the reply text."""
    name, p = provider_for(agent, api_key=api_key)
    if not p["key"] or p["key"] == "your_key_here":
        raise LLMError(f"{p['key_name']} is not set. Put your key in BugBait's .env file.")
    if not p["base"] or not (model or p["model"]):
        raise LLMError(f"The '{name}' provider needs an API base URL and a model name in .env.")

    resp = requests.post(
        f"{p['base'].rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json"},
        json={"model": model or p["model"], "messages": messages, "temperature": temperature},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise LLMError(f"{p['label']} error {resp.status_code}: {resp.text[:500]}")
    msg = resp.json()["choices"][0]["message"]
    return msg.get("content") or ""


def extract_json(text):
    """Pull a JSON value out of a model reply, tolerating ```json fences, reasoning text or extra prose."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)   # open models may include reasoning
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        raise LLMError(f"No JSON found in reply: {text[:300]}")
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(text[start:])
    return value
