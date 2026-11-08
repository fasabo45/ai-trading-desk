"""Journal, bench, alerts, kill switch. The boring seats that keep the desk honest."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
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
# ---------- talking to a console that cannot spell the token's name ----------
def make_output_safe(stream=None) -> None:
    """Never let an untrusted token name kill a cycle.

    Windows uses cp1252 for stdout when it is a pipe or a file, so printing a
    symbol like `木槌` raises UnicodeEncodeError. A token name is third-party data,
    which means the desk must survive anything the deployer types. Unencodable
    characters become `?` instead of taking the cycle down with them.

    Called once at the start of each entry point, because each one is its own
    process and only the first print needs it.
    """
    out = stream if stream is not None else sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(errors="replace")


def alert(text: str):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text, flush=True)          # flushed: a piped run must not look frozen
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
          "    The scan will run, but every survivor fails at the analyst seat.",
          flush=True)
    return True


# ---------- status line: a cycle must say what happened, not just dump a dict ----------
def human_wait(seconds: float) -> str:
    """A wait a human can read. Never negative: "due now" is not "-4m"."""
    seconds = max(0.0, float(seconds))
    if seconds < 1:
        return "now"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def cycle_line(stats: dict, equity: float, parked: int = 0,
               next_wait_s: float = 0.0) -> str:
    """One line per cycle that answers "is anything happening?".

    The dict this replaces read as `{'held': 38, 'entries': 0}`, which was true
    and looked like a hung process: those 38 pools were parked by design, because
    `new_pools` only returns minutes-old pools and the scanner has a 10-minute
    floor. So say "parked, next in 9m" instead of leaving the operator to guess.
    """
    parts = [f"{stats['seen']} seen"]
    if (kills := stats["free_kills"] + stats["chain_kills"]):
        parts.append(f"{kills} killed")
    if (rejects := stats["analyst_rejects"] + stats["risk_rejects"]):
        parts.append(f"{rejects} rejected")
    parts.append(f"{stats['entries']} entries")
    parts.append(f"equity ${equity:,.2f}")
    if parked:
        # Pools past the floor but not yet re-read (WATCH_BUDGET caps the re-reads per
        # cycle) are ripe and queued, not due in zero minutes.
        parts.append(f"{parked} parked, " + ("next ready now" if next_wait_s < 1
                                            else f"next ready in {human_wait(next_wait_s)}"))
    return "cycle " + " | ".join(parts)


def maturity_note(parked: int, min_age_minutes: float) -> str:
    """Why a fresh run looks idle. Said once, not every cycle."""
    return (f"[i] {parked} pools are parked because they are younger than the "
            f"{min_age_minutes:g}-minute floor.\n"
            f"    That is not a stall: the watchlist re-reads each one when it matures,\n"
            f"    so the first real decisions land within about {min_age_minutes:g} "
            f"minutes of a run starting.")


def undecided(stats: dict) -> bool:
    """True only while the desk has ruled on nothing yet.

    The maturity note is about an empty first ten minutes. Once a pool has been
    killed, rejected or bought, the run is visibly working and the note claiming
    decisions are still to come is just wrong.
    """
    return not (stats["free_kills"] or stats["chain_kills"]
                or stats["analyst_rejects"] or stats["risk_rejects"]
                or stats["entries"])


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
