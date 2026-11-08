"""What the desk looks like from outside, assembled into one dict.

The UI is a dumb renderer: it draws whatever `snapshot()` says and owns no rules
of its own. Every number here comes from the three files the desk already writes,
and nothing here writes to any of them.

    journal.jsonl        what the desk decided
    desk.db              what it is waiting on
    paper_ledger.json    what it holds

Reading is deliberately tolerant: a missing journal is an empty desk, not an
error, because a fresh install has never run a cycle.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from config import PAPER, SCAN
from desk import ops
from desk.broker import LEDGER
from desk.ops import JOURNAL
from desk.watchlist import Watchlist

DB = Path("desk.db")
FEED_LIMIT = 60


# ---------- journal ----------
def last_of(rows: list[dict], kind: str) -> dict | None:
    """The most recent row of a kind, or None. Rows are oldest first."""
    for row in reversed(rows):
        if row.get("kind") == kind:
            return row
    return None


def equity_curve(rows: list[dict]) -> list[dict]:
    """Equity after each cycle. The only series a paper desk actually has."""
    return [{"t": r["time"], "equity": r["equity"]}
            for r in rows if r.get("kind") == "cycle" and "equity" in r]


def funnel(stats: dict) -> dict:
    """The five seats as counts, with what fell off at each one.

    Everything is clamped at zero: a token that errors at seat 3 is counted in
    the cycle as seen but in nothing else, so naive subtraction can go negative
    and a negative funnel would be a lie.
    """
    seen = stats.get("seen", 0)
    parked, benched = stats.get("held", 0), stats.get("benched", 0)
    judged = max(0, seen - parked - benched)
    free_kills = stats.get("free_kills", 0)
    chain_kills = stats.get("chain_kills", 0)
    analyst_rejects = stats.get("analyst_rejects", 0)
    risk_rejects = stats.get("risk_rejects", 0)
    entries = stats.get("entries", 0)

    after_free = max(0, judged - free_kills)
    after_chain = max(0, after_free - chain_kills)
    after_analyst = max(0, after_chain - analyst_rejects)
    after_risk = max(0, after_analyst - risk_rejects)
    return {
        "seen": seen, "parked": parked, "benched": benched, "judged": judged,
        "stages": [
            {"key": "scanner", "label": "Scanner", "hint": "free cuts",
             "in": judged, "cut": free_kills},
            {"key": "chain", "label": "Chain facts", "hint": "RPC", "in": after_free,
             "cut": chain_kills},
            {"key": "analyst", "label": "Analyst", "hint": "Claude",
             "in": after_chain, "cut": analyst_rejects},
            {"key": "risk", "label": "Risk engine", "hint": "pure code",
             "in": after_analyst, "cut": risk_rejects},
            {"key": "broker", "label": "Approved", "hint": "paper",
             "in": after_risk, "cut": 0},
        ],
        "approved": entries,
    }


def reject_wall(rows: list[dict]) -> list[dict]:
    """Why tokens die, worst offender first. Rejections and refusals, one wall."""
    counts: Counter = Counter()
    for row in rows:
        kind = row.get("kind")
        if kind == "reject":
            counts[(row.get("stage", "free"), row.get("reason", "?"))] += 1
        elif kind == "decision" and not row.get("approved"):
            verdict = (row.get("thesis") or {}).get("verdict")
            stage = "analyst" if verdict not in (None, "valid_setup") else "risk"
            counts[(stage, row.get("reason", "?"))] += 1
    return [{"stage": stage, "reason": reason, "count": n}
            for (stage, reason), n in counts.most_common(12)]


def feed(rows: list[dict], limit: int = FEED_LIMIT) -> list[dict]:
    """The journal as a readable stream, newest first, big blobs stripped.

    `cycle` rows reuse ops.cycle_line(), so the browser and the console cannot
    describe the same cycle two different ways.
    """
    out = []
    for row in reversed(rows):
        kind = row.get("kind")
        if kind == "cycle":
            text = ops.cycle_line(row, row.get("equity") or 0.0)
        elif kind == "hold":
            text = f"{row.get('symbol', '?')} too young, parked"
        elif kind == "reject":
            text = f"{row.get('symbol', '?')} killed at {row.get('stage')}: {row.get('reason')}"
        elif kind == "decision":
            thesis = row.get("thesis") or {}
            verdict = thesis.get("verdict", "?")
            score = thesis.get("score", "-")
            text = (f"{row.get('symbol', '?')} {verdict} {score}/10 | "
                    f"{'approved' if row.get('approved') else row.get('reason', 'rejected')}")
        elif kind == "entry":
            text = f"BUY {row.get('symbol', '?')} qty {row.get('qty', 0):.4g} @ {row.get('fill', 0):.8g}"
        elif kind == "exit":
            pnl = row.get("pnl_usd")
            text = (f"SELL {row.get('symbol', '?')} ({row.get('reason')})"
                    + (f" pnl ${pnl}" if pnl is not None else ""))
        elif kind == "error":
            text = f"{row.get('stage')} error: {str(row.get('error'))[:120]}"
        else:
            text = kind or "?"
        out.append({"t": row.get("time"), "kind": kind, "text": text,
                    "symbol": row.get("symbol")})
        if len(out) >= limit:
            break
    return out


# ---------- ledger ----------
def ledger_state(path: Path = LEDGER, now: float | None = None) -> dict:
    """Open positions and closed trades, or an untouched starting book."""
    now = now or time.time()
    if not path.exists():
        return {"exists": False, "equity": None, "cash": None, "day_pnl": None,
                "day_start": None, "positions": [], "closed": []}
    book = json.loads(path.read_text(encoding="utf-8"))
    positions = [{"mint": mint, "symbol": p.get("symbol", "?"), "qty": p.get("qty", 0),
                  "entry": p.get("entry", 0), "last": p.get("last", 0),
                  "cost": p.get("cost", 0), "half_taken": p.get("half_taken", False),
                  "held_min": round((now - p.get("opened", now)) / 60, 1),
                  "pct": round((p.get("last", 0) / p.get("entry", 1) - 1) * 100, 1)
                  if p.get("entry") else 0.0}
                 for mint, p in (book.get("positions") or {}).items()]
    held = sum(p["qty"] * p["last"] for p in positions)
    return {"exists": True,
            "equity": round(book.get("cash", 0) + held, 2),
            "cash": round(book.get("cash", 0), 2),
            "day_start": book.get("day_start"),
            "day_pnl": round(book.get("cash", 0) + held - book.get("day_start", 0), 2)
            if book.get("day_start") is not None else None,
            "positions": sorted(positions, key=lambda p: p["cost"], reverse=True),
            "closed": (book.get("closed") or [])[-10:][::-1]}


# ---------- watchlist ----------
def watchlist_state(db_path: Path = DB, min_age_minutes: float = SCAN["min_age_minutes"],
                    limit: int = 8) -> dict:
    """Parked pools and how long until each one is worth reading again."""
    empty = {"parked": 0, "next_ready_s": 0.0, "soonest": []}
    if not Path(db_path).exists():
        return empty
    try:
        w = Watchlist(str(db_path), readonly=True)
        try:
            parked, next_ready_s = w.pending(min_age_minutes)
            rows = w.soonest(min_age_minutes, limit=limit)
        finally:
            w.db.close()      # a 2-second poll that leaks handles holds the file on Windows
    except sqlite3.Error:
        return empty          # table not created yet, or busy mid-write
    return {"parked": parked, "next_ready_s": next_ready_s,
            "soonest": [{"symbol": r["symbol"], "dex": r["dex"],
                         "waited_min": r["waited_min"], "ready_in_s": r["ready_in_s"]}
                        for r in rows]}


# ---------- the whole picture ----------
def snapshot(journal_path: Path = JOURNAL, db_path: Path = DB, ledger_path: Path = LEDGER,
             days: float = 1.0, feed_limit: int = FEED_LIMIT) -> dict:
    rows = ops.read_journal(days, journal_path)
    stats = last_of(rows, "cycle") or {}
    book = ledger_state(ledger_path)
    watch = watchlist_state(db_path)
    decision = last_of(rows, "decision") or {}
    last_cycle = last_of(rows, "cycle")
    age = None
    if last_cycle:
        age = round(time.time() - datetime.fromisoformat(last_cycle["time"]).timestamp(), 1)
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "starting_equity": PAPER["starting_equity_usd"],
        "min_age_minutes": SCAN["min_age_minutes"],
        "equity": book["equity"] if book["exists"] else stats.get("equity"),
        "day_pnl": book["day_pnl"] if book["exists"] else stats.get("day_pnl"),
        "cash": book["cash"],
        "ledger_exists": book["exists"],
        "pipeline": funnel(stats),
        "cycles": {"count": sum(1 for r in rows if r.get("kind") == "cycle"),
                   "last_at": last_cycle["time"] if last_cycle else None,
                   "last_age_s": age},
        "equity_curve": equity_curve(rows)[-240:],
        "rejects": reject_wall(rows),
        "watchlist": watch,
        "positions": book["positions"],
        "closed": book["closed"],
        "feed": feed(rows, feed_limit),
        "errors": [{"stage": stage, "count": n} for stage, n
                   in Counter(r.get("stage") for r in rows
                              if r.get("kind") == "error").most_common()],
        "health": {"key_ok": not ops.key_is_placeholder(os.getenv("ANTHROPIC_API_KEY")),
                   "kill_file": ops.KILL_FILE.exists(),
                   "mode": "shadow" if decision.get("shadow") else
                           ("paper" if decision else None)},
    }