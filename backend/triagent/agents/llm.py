"""Provider-independent LLM layer.

LLMs are used ONLY to phrase narratives over evidence that the deterministic
engine has already computed. They never compute indicators, prices or sizes.
Every LLM output is validated: it must be JSON, may cite only known evidence IDs,
and may not introduce numbers absent from the evidence. Invalid output is
discarded and the template narrative is used instead (and the rejection is logged).
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(role: str) -> tuple[str, str]:
    text = (PROMPT_DIR / f"{role.lower()}.md").read_text()
    m = re.search(r"prompt_version:\s*(\S+)", text)
    return text, (m.group(1) if m else "unversioned")


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def complete_json(self, system: str, user: str, model: str, temperature: float = 0.0) -> dict | None: ...


class NullProvider(LLMProvider):
    name = "none"

    def complete_json(self, system, user, model, temperature=0.0):
        return None


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.client = client or httpx.Client(timeout=60)

    def complete_json(self, system, user, model, temperature=0.0):
        if not self.api_key:
            return None
        r = self.client.post("https://api.anthropic.com/v1/messages",
                             headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                             json={"model": model or "claude-sonnet-5-5", "max_tokens": 1500, "temperature": temperature,
                                   "system": system, "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        return _parse_json(text)


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str | None = None, api_key: str | None = None, client: httpx.Client | None = None):
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.client = client or httpx.Client(timeout=60)

    def complete_json(self, system, user, model, temperature=0.0):
        if not self.api_key:
            return None
        r = self.client.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"},
                             json={"model": model, "temperature": temperature,
                                   "response_format": {"type": "json_object"},
                                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return _parse_json(r.json()["choices"][0]["message"]["content"])


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def get_provider(name: str) -> LLMProvider:
    return {"anthropic": AnthropicProvider, "openai_compatible": OpenAICompatibleProvider}.get(name, NullProvider)()


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def validate_narrative(out: dict | None, evidence_ids: set[str], evidence_text: str) -> tuple[bool, str]:
    """Reject narratives that cite unknown evidence or introduce numbers not present in the evidence."""
    if not out or not isinstance(out.get("thesis"), str):
        return False, "no/invalid JSON"
    cited = set(out.get("cited_ids") or [])
    unknown = cited - evidence_ids
    if unknown:
        return False, f"cites unknown evidence ids {sorted(unknown)[:5]}"
    allowed = set(_NUM.findall(evidence_text))
    stray = [n for n in _NUM.findall(out["thesis"]) if n not in allowed and len(n.strip("-").replace(".", "")) > 1]
    if stray:
        return False, f"introduces numbers not in evidence: {stray[:5]}"
    return True, "ok"
