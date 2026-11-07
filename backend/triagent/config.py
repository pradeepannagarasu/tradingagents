"""Configuration loading.

Order of precedence (highest first):
  1. Environment variables (TRIAGENT_* and a few well-known names)
  2. config/local.toml (git-ignored, user overrides)
  3. config/settings.toml (committed defaults)

Secrets (broker keys, LLM keys, bot tokens) are ONLY read from environment
variables and are never written to logs or returned by the API.
"""
from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("TRIAGENT_CONFIG_DIR", REPO_ROOT / "config"))


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    raw: dict[str, Any]
    database_url: str
    live_trading_env_flag: bool
    risk_signing_key: bytes
    secrets_present: dict[str, bool] = field(default_factory=dict)

    # convenience accessors -------------------------------------------------
    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    @property
    def mode(self) -> str:
        return os.environ.get("TRIAGENT_MODE", self.section("system").get("mode", "PAPER")).upper()

    @property
    def live_trading_enabled(self) -> bool:
        """Live trading requires BOTH the config flag and the env flag."""
        return bool(self.section("system").get("live_trading_enabled", False)) and self.live_trading_env_flag

    @property
    def weights(self) -> dict[str, float]:
        return dict(self.section("scoring").get("weights", {}))

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(self.section("scoring").get("thresholds", {}))


SECRET_ENV_NAMES = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_ACCESS_TOKEN",
    "UPSTOX_API_KEY",
    "DHAN_CLIENT_ID",
    "DHAN_ACCESS_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
]


def load_settings(config_dir: Path | None = None, overrides: dict | None = None) -> Settings:
    cdir = Path(config_dir or CONFIG_DIR)
    raw: dict[str, Any] = {}
    default = cdir / "settings.toml"
    if default.exists():
        raw = tomllib.loads(default.read_text())
    local = cdir / "local.toml"
    if local.exists():
        raw = _deep_merge(raw, tomllib.loads(local.read_text()))
    if overrides:
        raw = _deep_merge(raw, overrides)

    db_url = os.environ.get("DATABASE_URL", f"sqlite:///{REPO_ROOT / 'data' / 'triagent.db'}")
    key = os.environ.get("RISK_SIGNING_KEY", "")
    if not key:
        # Ephemeral per-process key: approvals cannot survive a restart, which is the safe default.
        key = os.urandom(32).hex()
    return Settings(
        raw=raw,
        database_url=db_url,
        live_trading_env_flag=_env_bool("LIVE_TRADING_ENABLED", False),
        risk_signing_key=key.encode(),
        secrets_present={n: bool(os.environ.get(n)) for n in SECRET_ENV_NAMES},
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
