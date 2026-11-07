"""Notifications. A signal alert is refused unless it carries full supporting information."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

import httpx

REQUIRED_SIGNAL_FIELDS = ["decision", "instrument", "timeframe", "entry", "stop_loss", "targets", "risk", "report_url"]


class IncompleteNotification(ValueError):
    pass


def format_decision(record: dict, base_url: str) -> dict:
    j = record["judge"]
    plan = j.get("plan") or {}
    risk = record.get("risk") or {}
    msg = {
        "decision": record["final_decision"],
        "instrument": f"{record['instrument']['symbol']} ({record['instrument']['exchange']})",
        "timeframe": record["horizon"],
        "entry": f"{plan.get('entry_low')}–{plan.get('entry_high')}" if plan else "n/a (NO TRADE)",
        "stop_loss": plan.get("stop_loss") if plan else "n/a",
        "targets": f"T1 {plan.get('target_1')} / T2 {plan.get('target_2')}" if plan else "n/a",
        "risk": (f"max loss at stop ₹{risk.get('max_loss_at_stop'):,.0f}; R:R {plan.get('risk_reward')}; "
                 f"risk engine {'APPROVED' if risk.get('approved') else 'REJECTED'}") if plan else
                "no position — " + "; ".join(j["reasons"][:1]),
        "report_url": f"{base_url.rstrip('/')}/decisions/{record['id']}",
        "simulated": record.get("simulated_data", False),
    }
    return msg


def render_text(msg: dict) -> str:
    missing = [f for f in REQUIRED_SIGNAL_FIELDS if not msg.get(f)]
    if missing:
        raise IncompleteNotification(f"Refusing to send notification without: {missing}")
    lines = [
        ("⚠ SIMULATED DATA — DEMO ONLY\n" if msg.get("simulated") else "") + f"{msg['decision']} — {msg['instrument']}",
        f"Timeframe: {msg['timeframe']}",
        f"Entry: {msg['entry']}",
        f"Stop-loss: {msg['stop_loss']}",
        f"Targets: {msg['targets']}",
        f"Risk: {msg['risk']}",
        f"Full research report: {msg['report_url']}",
        "Decision support only — not investment advice; outcomes are uncertain.",
    ]
    return "\n".join(lines)


class Notifier:
    def __init__(self, cfg: dict, client: httpx.Client | None = None):
        self.cfg = cfg
        self.client = client or httpx.Client(timeout=10)
        self.in_app: list[dict] = []

    def send_decision(self, record: dict, base_url: str) -> dict:
        text = render_text(format_decision(record, base_url))
        return self.send(f"Signal: {record['final_decision']} {record['instrument']['symbol']}", text)

    def send(self, subject: str, text: str) -> dict:
        results = {}
        if self.cfg.get("in_app_enabled", True):
            self.in_app.append({"subject": subject, "text": text})
            self.in_app = self.in_app[-200:]
            results["in_app"] = "ok"
        if self.cfg.get("telegram_enabled"):
            results["telegram"] = self._telegram(text)
        if self.cfg.get("email_enabled"):
            results["email"] = self._email(subject, text)
        return results

    def _telegram(self, text: str) -> str:
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if not (token and chat):
            return "skipped: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set"
        try:
            r = self.client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
            return "ok" if r.status_code == 200 else f"error {r.status_code}"
        except httpx.HTTPError as e:
            return f"error: {type(e).__name__}"  # never log the token-bearing URL

    def _email(self, subject: str, text: str) -> str:
        host, user, pwd, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL_TO"))
        if not (host and to):
            return "skipped: SMTP_HOST/ALERT_EMAIL_TO not set"
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = subject, user or "triagent@localhost", to
        m.set_content(text)
        try:
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as s:
                s.starttls()
                if user and pwd:
                    s.login(user, pwd)
                s.send_message(m)
            return "ok"
        except Exception as e:  # noqa: BLE001
            return f"error: {type(e).__name__}"
