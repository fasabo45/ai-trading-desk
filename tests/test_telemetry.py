"""The UI's data layer. No HTTP, no browser, no network, no keys.

    python -m unittest discover -s tests -v

The point of these tests is that the browser owns no rules: if a number is wrong
here, it is wrong on screen, and the screen is what someone acts on.
"""
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                  # noqa: E402
from desk import telemetry                                     # noqa: E402
from desk.broker import PaperBroker                            # noqa: E402
from desk.watchlist import Watchlist                           # noqa: E402


def now_iso(offset_s: float = 0) -> str:
    return datetime.fromtimestamp(time.time() + offset_s, timezone.utc).isoformat()


def cycle_row(**kw):
    row = {"time": now_iso(), "kind": "cycle", "seen": 10, "benched": 0, "held": 0,
           "free_kills": 0, "chain_kills": 0, "analyst_rejects": 0, "risk_rejects": 0,
           "entries": 0, "equity": 1000.0, "day_pnl": 0.0}
    row.update(kw)
    return row


class Funnel(unittest.TestCase):
    def test_every_token_is_accounted_for(self):
        p = telemetry.funnel(cycle_row(seen=10, held=3, free_kills=2, chain_kills=1,
                                      analyst_rejects=2, risk_rejects=1, entries=1))
        self.assertEqual(p["judged"], 7)                 # 10 seen, 3 parked
        self.assertEqual([s["in"] for s in p["stages"]], [7, 5, 4, 2, 1])
        self.assertEqual(p["approved"], 1)

    def test_it_never_shows_negative_counts(self):
        # A token that errors at seat 3 is seen but counted nowhere else, so naive
        # subtraction can go negative, and a negative funnel is a lie.
        p = telemetry.funnel(cycle_row(seen=2, held=1, free_kills=5, chain_kills=5))
        self.assertTrue(all(s["in"] >= 0 and s["cut"] >= 0 for s in p["stages"]))

    def test_an_empty_cycle_is_all_zeroes(self):
        p = telemetry.funnel({})
        self.assertEqual([s["in"] for s in p["stages"]], [0] * 5)
        self.assertEqual(p["approved"], 0)

    def test_the_cuts_match_what_the_cycle_counted(self):
        p = telemetry.funnel(cycle_row(seen=6, free_kills=2, chain_kills=1,
                                       analyst_rejects=1, risk_rejects=1, entries=1))
        self.assertEqual([s["cut"] for s in p["stages"]], [2, 1, 1, 1, 0])


class RejectWall(unittest.TestCase):
    def test_rejects_and_refusals_share_one_wall(self):
        rows = [
            {"kind": "reject", "stage": "free", "reason": "liquidity"},
            {"kind": "reject", "stage": "free", "reason": "liquidity"},
            {"kind": "reject", "stage": "chain", "reason": "top_10"},
            {"kind": "decision", "approved": False, "reason": "score",
             "thesis": {"verdict": "skip"}},
            {"kind": "decision", "approved": False, "reason": "too big",
             "thesis": {"verdict": "valid_setup"}},
        ]
        wall = telemetry.reject_wall(rows)
        self.assertEqual(wall[0], {"stage": "free", "reason": "liquidity", "count": 2})
        stages = {(w["stage"], w["reason"]) for w in wall}
        self.assertIn(("analyst", "score"), stages)      # a verdict, not a risk refusal
        self.assertIn(("risk", "too big"), stages)       # approved thesis the code refused

    def test_approved_decisions_are_not_on_the_wall(self):
        rows = [{"kind": "decision", "approved": True, "reason": "ok",
                 "thesis": {"verdict": "valid_setup"}}]
        self.assertEqual(telemetry.reject_wall(rows), [])

    def test_the_wall_is_capped_and_ordered(self):
        rows = [{"kind": "reject", "stage": "free", "reason": f"r{i}"} for i in range(30)]
        self.assertEqual(len(telemetry.reject_wall(rows)), 12)


class Feed(unittest.TestCase):
    def test_newest_first(self):
        rows = [cycle_row(time=now_iso(-5), seen=1), cycle_row(seen=2)]
        self.assertIn("2 seen", telemetry.feed(rows)[0]["text"])
        self.assertIn("1 seen", telemetry.feed(rows)[1]["text"])

    def test_a_cycle_row_is_rendered_by_the_same_code_as_the_console(self):
        from desk import ops
        row = cycle_row(seen=4, free_kills=1, equity=1000.0)
        self.assertEqual(telemetry.feed([row])[0]["text"], ops.cycle_line(row, 1000.0))

    def test_big_blobs_are_stripped_out(self):
        # The journal stores the whole token dict and thesis. The browser must not
        # be sent that: it needs a line of text.
        rows = [{"time": now_iso(), "kind": "reject", "stage": "free", "reason": "mcap",
                 "symbol": "DOG", "token": {"huge": "x" * 5000}}]
        entry = telemetry.feed(rows)[0]
        self.assertLess(len(json.dumps(entry)), 400)
        self.assertEqual(entry["kind"], "reject")

    def test_the_limit_is_respected(self):
        rows = [cycle_row(seen=i) for i in range(100)]
        self.assertEqual(len(telemetry.feed(rows, limit=5)), 5)

    def test_a_symbol_with_markup_is_just_text(self):
        rows = [{"time": now_iso(), "kind": "reject", "stage": "free", "reason": "mcap",
                 "symbol": "<img src=x onerror=alert(1)>"}]
        self.assertIn("<img", telemetry.feed(rows)[0]["text"])   # escaped by the renderer


class WatchlistState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "desk.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_desk_that_never_ran_has_no_watchlist_and_no_crash(self):
        self.assertEqual(telemetry.watchlist_state(self.db)["parked"], 0)

    def test_parked_pools_are_reported_with_a_countdown(self):
        w = Watchlist(str(self.db))
        w.hold({"mint": "A", "pool": "P1", "dex": "pump-fun", "symbol": "DOG"})
        w.hold({"mint": "B", "pool": None, "dex": "pump-fun", "symbol": "NOADDR"})
        w.db.close()
        state = telemetry.watchlist_state(self.db, min_age_minutes=10)
        self.assertEqual(state["parked"], 1)             # the addressless one cannot mature
        self.assertAlmostEqual(state["next_ready_s"], 600, delta=5)
        self.assertEqual(state["soonest"][0]["symbol"], "DOG")

    def test_reading_does_not_create_the_database(self):
        missing = Path(self.tmp.name) / "nope.db"
        telemetry.watchlist_state(missing)
        self.assertFalse(missing.exists())


class LedgerState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "paper_ledger.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_ledger_is_not_an_error(self):
        s = telemetry.ledger_state(self.path)
        self.assertFalse(s["exists"])
        self.assertIsNone(s["equity"])
        self.assertEqual(s["positions"], [])

    def test_positions_carry_their_own_pnl(self):
        b = PaperBroker(self.path)
        b.buy("M", "DOG", 20, 1.0)
        b.s["positions"]["M"]["last"] = 1.2             # a mark, as a quote would
        b._save()
        s = telemetry.ledger_state(self.path)
        self.assertEqual(len(s["positions"]), 1)
        self.assertEqual(s["positions"][0]["symbol"], "DOG")
        self.assertGreater(s["positions"][0]["pct"], 0)
        self.assertGreater(s["equity"], 0)

    def test_a_closed_trade_appears_once(self):
        b = PaperBroker(self.path)
        b.buy("M", "DOG", 20, 1.0)
        b.sell("M", 1.0, 1.3, "take half at 2x")
        s = telemetry.ledger_state(self.path)
        self.assertEqual(s["positions"], [])
        self.assertEqual(s["closed"][0]["symbol"], "DOG")
        self.assertIn("pnl_usd", s["closed"][0])


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.journal = self.dir / "journal.jsonl"
        self.db = self.dir / "desk.db"
        self.ledger = self.dir / "paper_ledger.json"

    def tearDown(self):
        self.tmp.cleanup()

    def snap(self, **kw):
        return telemetry.snapshot(journal_path=self.journal, db_path=self.db,
                                  ledger_path=self.ledger, **kw)

    def test_a_brand_new_install_renders(self):
        s = self.snap()
        self.assertIsNone(s["equity"])
        self.assertEqual(s["cycles"]["count"], 0)
        self.assertEqual(s["feed"], [])
        self.assertIsNone(s["health"]["mode"])
        self.assertEqual(s["starting_equity"], config.PAPER["starting_equity_usd"])

    def test_the_last_cycle_supplies_the_headline_when_there_is_no_ledger(self):
        self.journal.write_text("\n".join(json.dumps(r) for r in [
            cycle_row(equity=1000.0), cycle_row(equity=1042.5, day_pnl=42.5)
        ]), encoding="utf-8")
        s = self.snap()
        self.assertEqual(s["equity"], 1042.5)
        self.assertEqual(s["day_pnl"], 42.5)
        self.assertEqual(s["cycles"]["count"], 2)

    def test_a_shadow_decision_marks_the_mode_as_shadow(self):
        self.journal.write_text(json.dumps({
            "time": now_iso(), "kind": "decision", "approved": True, "shadow": True,
            "thesis": {"verdict": "valid_setup", "score": 8}}) + "\n", encoding="utf-8")
        self.assertEqual(self.snap()["health"]["mode"], "shadow")

    def test_a_truncated_journal_line_does_not_blind_the_view(self):
        self.journal.write_text(json.dumps(cycle_row(equity=999.0)) + "\n{\"kind\": \"cyc",
                                encoding="utf-8")
        s = self.snap()
        self.assertEqual(s["cycles"]["count"], 1)
        self.assertEqual(s["equity"], 999.0)

    def test_old_rows_fall_outside_the_window(self):
        self.journal.write_text("\n".join(json.dumps(r) for r in [
            cycle_row(time=now_iso(-3 * 24 * 3600), equity=1.0),
            cycle_row(equity=2.0),
        ]), encoding="utf-8")
        s = self.snap(days=1.0)
        self.assertEqual(s["cycles"]["count"], 1)
        self.assertEqual(s["equity"], 2.0)

    def test_the_ledger_wins_over_the_last_cycle_line(self):
        # The ledger is the account of record; a cycle line is a snapshot of it.
        b = PaperBroker(self.ledger)
        b.buy("M", "DOG", 20, 1.0)
        self.journal.write_text(json.dumps(cycle_row(equity=1000.0)) + "\n", encoding="utf-8")
        s = self.snap()
        self.assertTrue(s["ledger_exists"])
        self.assertNotEqual(s["equity"], 1000.0)
        self.assertEqual(s["positions"][0]["symbol"], "DOG")


if __name__ == "__main__":
    unittest.main()