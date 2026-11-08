"""Seat 1.5: pools seen too young to judge.

`new_pools` hands back pools about two minutes old, which is younger than the
10-minute floor the scanner enforces. Those pools are not bad, they are just
early, and their rows disappear from `new_pools` long before they mature.

So the desk keeps their address and holds them. When one has been on the list
for longer than the age floor, `due()` returns it and the scanner re-reads it
by name. The wait is measured from first sight, which is never later than the
pool's own age, so a returned pool is always at least as old as it looks.
"""
from __future__ import annotations

import sqlite3
import time


class Watchlist:
    def __init__(self, path: str = "desk.db"):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS watchlist("
            "mint TEXT PRIMARY KEY, pool TEXT, dex TEXT, symbol TEXT, held_at REAL)"
        )
        self.db.commit()

    def hold(self, t: dict) -> bool:
        """Park a too-young pool. Returns True only the first time it is seen.

        INSERT OR IGNORE, not REPLACE: a pool that keeps reappearing in
        `new_pools` must not restart its own clock every cycle.
        """
        cur = self.db.execute(
            "INSERT OR IGNORE INTO watchlist VALUES (?,?,?,?,?)",
            (t["mint"], t.get("pool"), t.get("dex"), t.get("symbol"), time.time()),
        )
        self.db.commit()
        return cur.rowcount == 1

    def due(self, min_age_minutes: float, max_age_hours: float) -> list[dict]:
        """Pools that have waited long enough to be worth re-reading, oldest first."""
        now = time.time()
        out = []
        for mint, pool, dex, symbol, held_at in self.db.execute(
                "SELECT mint, pool, dex, symbol, held_at FROM watchlist").fetchall():
            waited = (now - held_at) / 60
            if waited < min_age_minutes:
                continue
            if waited > max_age_hours * 60:      # hopeless, stop paying for it
                self.drop(mint)
                continue
            if not pool:                         # cannot re-read without an address
                self.drop(mint)
                continue
            out.append({"mint": mint, "pool": pool, "dex": dex, "symbol": symbol})
        return sorted(out, key=lambda w: w["symbol"] or "")

    def drop(self, mint: str) -> None:
        self.db.execute("DELETE FROM watchlist WHERE mint=?", (mint,))
        self.db.commit()

    def pending(self, min_age_minutes: float) -> tuple[int, float]:
        """(pools still waiting, seconds until the next one matures).

        Addressless pools are excluded because `due()` drops them: they can never
        mature, so counting them would promise a re-read that never comes.
        """
        now = time.time()
        waits = [min_age_minutes * 60 - (now - held_at)
                 for pool, held_at in self.db.execute("SELECT pool, held_at FROM watchlist")
                 if pool]
        return len(waits), max(min(waits, default=0.0), 0.0)

    def size(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]