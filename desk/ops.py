"""Journal, bench, alerts, kill switch. The boring seats that keep the desk honest."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import BENCH_MINUTES, DEFAULT_BENCH

JOURNAL = Path("journal.jsonl")
KILL_FILE = Path("KILL")

MODEL_HINT = ("that model id was rejected. Set ANALYST_MODEL or REVIEW_MODEL in .env "
              "to one your key can use, or edit the defaults in config.py")


def model_not_found(e: Exception) -> bool:
    """True only when the API says the model id itself is unknown.

    Narrow on purpose: a 500 or a timeout must not be reported as a config typo.
    """
    return getattr(e, "status_code", None) == 404


# ---------- journal: every decision, including every rejection ----------
def journal(kind: str, **fields) -> dict:
    row = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return row


# ---------- bench: a rejected token sits out, for as long as its failure deserves ----------
class Bench:
    def __init__(self, path: str = "desk.db"):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS bench(mint TEXT PRIMARY KEY, reason TEXT, until REAL)")
        self.db.commit()

    def benched(self, mint: str) -> bool:
        r = self.db.execute("SELECT until FROM bench WHERE mint=?", (mint,)).fetchone()
        return bool(r and r[0] > time.time())

    def sit(self, mint: str, reason: str):
        mins = BENCH_MINUTES.get(reason, DEFAULT_BENCH)
        self.db.execute("INSERT OR REPLACE INTO bench VALUES (?,?,?)",
                        (mint, reason, time.time() + mins * 60))
        self.db.commit()


# ---------- alerts: Telegram if configured, stdout otherwise ----------
def alert(text: str):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text)
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text,
                                "disable_web_page_preview": True}, timeout=10)
        except Exception as e:
            print(f"telegram failed: {e}")


# ---------- kill switch: create a file named KILL and the desk stops ----------
def killed() -> bool:
    return KILL_FILE.exists()


# ---------- startup guard: a missing key is a silent, expensive nothing ----------
def key_is_placeholder(key: str | None) -> bool:
    """Empty, absent, or still the `sk-ant-...` from .env.example.

    The example placeholder is truthy, so a plain presence check would report the
    desk as ready while it holds a key that cannot possibly authenticate.
    """
    key = (key or "").strip()
    return not key or key.endswith("...")


def warn_missing_key() -> bool:
    """Without a key the scan still runs and every survivor dies at seat 3, cycle
    after cycle, forever. Say so once instead of journaling it 1,440 times a day."""
    if not key_is_placeholder(os.getenv("ANTHROPIC_API_KEY")):
        return False
    print("[!] ANTHROPIC_API_KEY is unset or still the .env.example placeholder.\n"
          "    The scan will run, but every survivor fails at the analyst seat.")
    return True


def load_env(path: str = ".env"):
    """Tiny .env loader so the desk has no extra dependency. Real env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
