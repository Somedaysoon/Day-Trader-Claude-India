"""trader-risk: the pick sanity check. Charge RATES are placeholders; the tests check the logic."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load

risk = load("trader-risk", "check")
size = load("trader-budget", "size")
exit_ = load("trader-exit", "exit")

PICK = {"symbol": "AAA", "side": "buy", "qty": 276, "entry": 102.0, "stop": 100.4, "target": 105.2, "tick": 0.05,
        "group": "cas", "date": "2026-11-23", "entry_time": "9:45 AM", "flat_by": "15:05"}
CFG = {"budget": 100000}


def now(text):
    return risk.parse_now(text)


def go(text="2026-11-23 10:15", pick=None, cfg=None, prices=None, charges=None):
    p = dict(PICK)
    p.update(pick or {})
    c = dict(CFG)
    c.update(cfg or {})
    return risk.check(p, c, prices, now(text), charges)


def status(res, name):
    return [r["status"] for r in res["checks"] if r["check"] == name]


def detail(res, name):
    return [r["detail"] for r in res["checks"] if r["check"] == name][0]


def free_charges():
    cfg = dict(risk.costs.load(), brokerage={"flat": 0.0, "pct": 0.0}, stt_pct_sell=0, exchange_txn_pct=0,
               sebi_per_crore=0, stamp_pct_buy=0, gst_pct=0, verified=True)
    return cfg


def prices(p, time="2026-11-23T04:44:00Z", sym="AAA"):
    return {"prices": {sym: p}, "detail": {sym: {"time": time}}}


class Baseline(unittest.TestCase):
    def test_a_good_pick_passes(self):
        r = go()
        self.assertEqual((r["verdict"], r["blocks"]), ("PASS", []))
        for name in ("pick", "today", "window", "stop", "size", "loss", "reward:risk", "costs"):
            self.assertEqual(status(r, name), ["pass"], name)
        self.assertEqual(r["checked_at_ist"], "2026-11-23 10:15:00")
        self.assertEqual(len(r["not_checked"]), 4)

    def test_it_always_says_what_it_did_not_check(self):
        text = risk.report(go())
        for want in ("price bands and circuit limits", "surveillance and trade-for-trade lists",
                     "bid-ask spread and market depth", "margin and your broker's own risk checks",
                     "Nothing was placed: this tool only checks.", "Education and tooling, not financial advice.",
                     "PASS: nothing in your rules stops this pick. That is not a reason to take it."):
            self.assertIn(want, text)

    def test_warnings_for_missing_price_and_placeholder_charges(self):
        r = go()
        self.assertEqual((status(r, "price"), status(r, "charges")), (["warn"], ["warn"]))
        self.assertIn("no price to check against", detail(r, "price"))
        self.assertEqual(len(r["warnings"]), 2)
        self.assertEqual(status(go(charges=free_charges()), "charges"), [])


class PickFacts(unittest.TestCase):
    def blocked(self, **pick):
        r = go(pick=pick)
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertEqual([c["check"] for c in r["checks"]], ["pick"])      # stops at the first failing stage
        return detail(r, "pick")

    def test_sides_quantities_and_prices(self):
        self.assertIn("longs (buy) only", self.blocked(side="sell"))
        self.assertIn("longs (buy) only", self.blocked(side=None))
        for q in (0, -5, 2.5, "276", True, None):
            self.assertIn("whole number of shares above 0", self.blocked(qty=q), repr(q))
        self.assertIn("the stop must be above 0", self.blocked(stop=None))
        self.assertIn("the target must be above 0", self.blocked(target=0))
        self.assertIn("the entry must be above 0", self.blocked(entry="cheap"))

    def test_symbols(self):
        self.assertIn("not an NSE stock symbol", self.blocked(symbol="^NSEI"))
        self.assertIn("not an NSE stock symbol", self.blocked(symbol="two words"))
        self.assertIn("not an NSE stock symbol", self.blocked(symbol=""))
        self.assertEqual(go(pick={"symbol": "M&M"})["verdict"], "PASS")
        self.assertEqual(go(pick={"symbol": "bajaj-auto"})["verdict"], "PASS")
        self.assertEqual(go(pick={"symbol": "reliance.ns"})["symbol"], "RELIANCE")

    def test_blocked_list(self):
        r = go(cfg={"blocked": ["aaa"]})
        self.assertIn("AAA is on your blocked list", detail(r, "pick"))

    def test_a_pick_that_is_not_a_dict(self):
        with self.assertRaises(risk.RiskError):
            risk.check([1, 2], {}, None, now("2026-11-23 10:15"))


class Today(unittest.TestCase):
    def test_another_days_pick(self):
        r = go(pick={"date": "2026-11-20"})
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("the pick is for 2026-11-20 and today is 2026-11-23", detail(r, "today"))


class Window(unittest.TestCase):
    def win(self, text, **kw):
        r = go(text, **kw)
        return status(r, "window")[0], detail(r, "window")

    def test_closed_days(self):
        st, why = self.win("2026-11-24 10:15", pick={"date": "2026-11-24"})
        self.assertEqual(st, "block")
        self.assertIn("the NSE is closed today (NSE holiday: Prakash Gurpurb", why)
        st, why = self.win("2026-11-28 10:15", pick={"date": "2026-11-28"})
        self.assertEqual((st, why), ("block", "the NSE is closed today (weekend)"))

    def test_the_auctions_and_the_hours_around_them(self):
        self.assertIn("pre-open auction", self.win("2026-11-23 09:05")[1])
        self.assertIn("the market is closed (continuous trading is 9:15 AM to 3:15 PM IST)", self.win("2026-11-23 08:30")[1])
        st, why = self.win("2026-11-23 15:20")
        self.assertEqual(st, "block")
        self.assertIn("closing auction session is running: continuous trading ended at 3:15 PM", why)
        self.assertIn("the market is closed", self.win("2026-11-23 16:00")[1])

    def test_entry_window_edges(self):
        self.assertEqual(self.win("2026-11-23 09:29")[0], "block")
        self.assertIn("before no_entries_before 09:30", self.win("2026-11-23 09:29")[1])
        self.assertEqual(self.win("2026-11-23 09:30")[0], "pass")
        self.assertEqual(self.win("2026-11-23 14:44")[0], "pass")
        st, why = self.win("2026-11-23 14:45")
        self.assertEqual(st, "block")
        self.assertIn("at or after 14:45, the last time for a new entry", why)

    def test_the_stocks_group_sets_the_cutoff(self):
        self.assertEqual(self.win("2026-11-23 14:50", pick={"group": "other"})[0], "pass")      # runs to 15:30
        self.assertEqual(self.win("2026-11-23 15:00", pick={"group": "other"})[0], "block")
        self.assertEqual(self.win("2026-11-23 14:50", pick={"group": "any"})[0], "block")

    def test_from_7_dec_every_group_runs_to_1530(self):
        pick = {"date": "2026-12-07"}
        self.assertEqual(self.win("2026-12-07 14:55", pick=pick)[0], "pass")
        self.assertEqual(self.win("2026-12-07 15:00", pick=pick)[0], "block")
        st, why = self.win("2026-12-07 15:20", pick=pick)
        self.assertIn("at or after 15:00", why)                 # open, but past the last time for an entry

    def test_flat_by_and_config_overrides(self):
        st, why = self.win("2026-11-23 14:35", pick={"flat_by": "14:30"})
        self.assertEqual(st, "block")
        self.assertIn("at or after the flat-by time 14:30", why)
        self.assertEqual(self.win("2026-11-23 14:35", cfg={"no_entries_after": "14:40"})[0], "pass")
        self.assertEqual(self.win("2026-11-23 14:41", cfg={"no_entries_after": "14:40"})[0], "block")
        self.assertEqual(self.win("2026-11-23 09:40", cfg={"no_entries_before": "09:45"})[0], "block")
        self.assertEqual(self.win("2026-11-23 14:44", cfg={"no_entries_after": "15:25"})[0], "pass")   # clamped to the end
        self.assertIn("entries until 14:45", self.win("2026-11-23 10:15")[1])

    def test_a_bad_config_time_is_a_plain_error(self):
        with self.assertRaises(risk.RiskError) as cm:
            go(cfg={"no_entries_before": "9.30"})
        self.assertIn("Indian time", str(cm.exception))


class Stop(unittest.TestCase):
    def test_wrong_side_stops_and_targets(self):
        r = go(pick={"stop": 102.0})
        self.assertIn("at or above the entry 102.00", detail(r, "stop"))
        self.assertEqual(r["verdict"], "BLOCK")
        r = go(pick={"stop": 103.0})
        self.assertEqual(status(r, "stop"), ["block"])
        r = go(pick={"target": 102.0})
        self.assertIn("the target 102.00 is at or below the entry", detail(r, "stop"))

    def test_a_blocked_stop_stops_the_later_checks(self):
        r = go(pick={"stop": 103.0})
        self.assertNotIn("loss", [c["check"] for c in r["checks"]])


class Price(unittest.TestCase):
    def test_a_price_inside_the_stop_and_target(self):
        r = go(prices=prices(102.0))
        self.assertEqual(status(r, "price"), ["pass"])
        self.assertIn("inside the stop and target", detail(r, "price"))
        r = go(prices=prices(101.0))                                  # lower than the entry: still fine
        self.assertEqual(status(r, "price"), ["pass"])

    def test_already_at_the_stop_or_the_target(self):
        r = go(prices=prices(100.4))
        self.assertEqual((r["verdict"], status(r, "price")), ("BLOCK", ["block"]))
        self.assertIn("already at or under the stop 100.40", detail(r, "price"))
        r = go(prices=prices(105.2))
        self.assertIn("already at or over the target 105.20: the move has happened", detail(r, "price"))

    def test_a_price_above_the_entry_raises_the_risk_checked(self):
        r = go(prices=prices(103.2))                                   # 276 x 2.80 = 772 over the Rs 500 limit
        self.assertEqual(status(r, "price"), ["warn"])
        self.assertIn("the risk is checked at 103.20", detail(r, "price"))
        self.assertEqual(status(r, "loss"), ["block"])
        self.assertEqual(r["verdict"], "BLOCK")
        r = go(prices=prices(102.05))                                  # a tick over: 276 x 1.65 = 455 plus costs
        self.assertEqual(status(r, "price"), ["warn"])

    def test_a_stale_price_is_not_used(self):
        r = go(prices=prices(103.2, time="2026-11-23T04:20:00Z"))
        self.assertEqual(status(r, "price"), ["warn"])
        self.assertIn("too old to use", detail(r, "price"))
        self.assertEqual(status(r, "loss"), ["pass"])
        r = go(prices=prices(103.2, time=None))
        self.assertIn("undated", detail(r, "price"))

    def test_another_symbols_price_is_ignored(self):
        r = go(prices=prices(103.2, sym="BBB"))
        self.assertIn("no price to check against", detail(r, "price"))


class Size(unittest.TestCase):
    def test_order_position_and_cash_limits(self):
        r = go(pick={"qty": 800})
        self.assertIn("Rs 81,600 is over your Rs 75,000 max order", detail(r, "size"))
        r = go(pick={"qty": 5})
        self.assertIn("Rs 510 is under your Rs 1,000 minimum order", detail(r, "size"))
        r = go(pick={"qty": 400}, cfg={"budget": 50000})
        self.assertIn("over 75% of your Rs 50,000 budget (Rs 37,500)", detail(r, "size"))
        r = go(pick={"qty": 600}, cfg={"cash_buffer_pct": 50})
        self.assertIn("more than your cash after the 50% buffer (Rs 50,000): no leverage is assumed", detail(r, "size"))
        self.assertEqual(go(cfg={"budget": None})["verdict"], "BLOCK")
        self.assertIn("no budget in the config", detail(go(cfg={"budget": None}), "size"))


class Money(unittest.TestCase):
    def test_the_loss_limit_is_all_in_and_sharp_at_the_edge(self):
        self.assertEqual(status(go(pick={"qty": 276}), "loss"), ["pass"])
        r = go(pick={"qty": 277})
        self.assertEqual(status(r, "loss"), ["block"])
        self.assertIn("all-in", detail(r, "loss"))
        self.assertIn("over your Rs 500 max loss per trade", detail(r, "loss"))

    def test_the_numbers_are_recomputed_not_trusted(self):
        r = go(pick={"qty": 400, "loss_at_stop": 1.0, "costs": {"loss_at_stop": 1.0}})
        self.assertEqual(status(r, "loss"), ["block"])

    def test_without_costs_it_is_plain_arithmetic(self):
        self.assertEqual(status(go(pick={"qty": 312}, charges=free_charges(), cfg={"slippage_pct": 0}), "loss"), ["pass"])
        self.assertEqual(status(go(pick={"qty": 313}, charges=free_charges(), cfg={"slippage_pct": 0}), "loss"), ["block"])

    def test_reward_risk(self):
        r = go(pick={"target": 102.5})
        self.assertIn("reward:risk 0.31 is under your minimum 1", detail(r, "reward:risk"))
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertEqual(status(go(cfg={"min_reward_risk": 2.5}), "reward:risk"), ["block"])
        self.assertIn("2.00 before costs, 1.65 after", detail(go(), "reward:risk"))

    def test_the_cost_gate(self):
        tight = {"qty": 200, "stop": 101.9, "target": 102.2}          # Rs 0.20 a share of reward
        r = go(pick=tight)
        self.assertEqual(status(r, "costs"), ["block"])
        self.assertIn("charges and slippage take 105.2% of the reward at the target (you allow 25%)", detail(r, "costs"))
        self.assertIn("Rs 40 gross becomes -Rs 2.07 net", detail(r, "costs"))     # it loses money even if the target hits
        self.assertEqual(status(go(pick=tight, cfg={"max_cost_pct_of_reward": 110}), "costs"), ["pass"])
        self.assertIn("take 6.7% of the reward (limit 25%)", detail(go(), "costs"))

    def test_slippage_comes_from_the_config(self):
        r = go(cfg={"slippage_pct": 0.5})
        self.assertEqual(status(r, "costs"), ["block"])


class Signal(unittest.TestCase):
    def test_a_stale_signal_warns_but_does_not_block(self):
        r = go("2026-11-23 10:30")
        self.assertEqual(status(r, "signal"), ["warn"])
        self.assertIn("the signal was 45 minutes ago (9:45 AM)", detail(r, "signal"))
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(status(go("2026-11-23 10:10"), "signal"), [])
        self.assertEqual(status(go("2026-11-23 10:30", cfg={"max_signal_age_minutes": 60}), "signal"), [])

    def test_clock_parsing(self):
        self.assertEqual(risk.parse_clock("9:45 AM"), datetime.time(9, 45))
        self.assertEqual(risk.parse_clock("3:05 pm"), datetime.time(15, 5))
        self.assertIsNone(risk.parse_clock("soon"))
        self.assertIsNone(risk.parse_clock("13:00 PM"))


class Calendar(unittest.TestCase):
    def test_a_year_the_holiday_list_does_not_cover(self):
        r = go("2027-01-20 10:15", pick={"date": "2027-01-20"})
        self.assertEqual(status(r, "calendar"), ["warn"])
        self.assertIn("holiday list covers 2026 only", detail(r, "calendar"))
        self.assertEqual(r["verdict"], "PASS")


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def p(self, name):
        return os.path.join(self.tmp, name)

    def put(self, name, doc):
        with open(self.p(name), "w") as fh:
            json.dump(doc, fh)

    def cli(self, *extra, **files):
        self.put("sizing.json", PICK)
        self.put("config.json", CFG)
        argv = ["--sizing", self.p("sizing.json"), "--config", self.p("config.json"), "--now", "2026-11-23 10:15",
                "--out", self.p("risk.json")] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = risk.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_pass_writes_the_verdict(self):
        code, out, _ = self.cli()
        self.assertEqual(code, 0)
        with open(self.p("risk.json")) as fh:
            doc = json.load(fh)
        for k, v in (("created_by", "trader-risk"), ("schema_version", 2), ("exchange", "NSE"), ("currency", "INR"),
                     ("symbol", "AAA"), ("verdict", "PASS"), ("checked_at_ist", "2026-11-23 10:15:00")):
            self.assertEqual(doc[k], v, k)
        self.assertEqual(doc["pick"]["qty"], 276)
        self.assertIn("Nothing was placed", doc["note"])
        self.assertEqual(len(doc["not_checked"]), 4)
        self.assertIn("Wrote the verdict to", out)

    def test_block_exits_1_and_no_write_skips_the_file(self):
        self.put("sizing.json", dict(PICK, qty=277))
        self.put("config.json", CFG)
        base = ["--sizing", self.p("sizing.json"), "--config", self.p("config.json"), "--now", "2026-11-23 10:15",
                "--out", self.p("risk.json")]
        out = io.StringIO()
        with redirect_stdout(out):
            code = risk.main(base + ["--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("BLOCK: do not take this pick as it stands.", out.getvalue())
        self.assertFalse(os.path.exists(self.p("risk.json")))
        with redirect_stdout(io.StringIO()):
            risk.main(base)
        with open(self.p("risk.json")) as fh:
            self.assertEqual(json.load(fh)["verdict"], "BLOCK")

    def test_json_output(self):
        code, out, _ = self.cli("--json", "--no-write")
        doc = json.loads(out)
        self.assertEqual((doc["verdict"], doc["symbol"]), ("PASS", "AAA"))

    def test_prices_file(self):
        self.put("prices.json", prices(103.2))
        code, out, _ = self.cli("--prices", self.p("prices.json"), "--no-write")
        self.assertEqual(code, 1)
        self.assertIn("the risk is checked at 103.20", out)

    def test_plain_errors(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = risk.main(["--sizing", self.p("nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("no sizing (run trader-budget --write first)", err.getvalue())
        with open(self.p("bad.json"), "w") as fh:
            fh.write("{oops")
        err = io.StringIO()
        with redirect_stderr(err):
            code = risk.main(["--sizing", self.p("bad.json")])
        self.assertIn("is not valid JSON", err.getvalue())
        self.put("sizing.json", PICK)
        err = io.StringIO()
        with redirect_stderr(err):
            code = risk.main(["--sizing", self.p("sizing.json"), "--config", self.p("none.json"), "--now", "later"])
        self.assertIn("Indian time", err.getvalue())


class Pipeline(unittest.TestCase):
    def test_exit_plan_to_sized_pick_to_risk_verdict(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        plan, sizing, verdict = (os.path.join(tmp, n) for n in ("plan.json", "sizing.json", "risk.json"))
        cfg = os.path.join(tmp, "config.json")
        with open(cfg, "w") as fh:
            json.dump({"budget": 100000, "max_loss_per_trade_inr": 500}, fh)
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            self.assertEqual(exit_.main(["plan", "--symbol", "AAA", "--entry", "102.0", "--or-high", "101.3",
                                         "--or-low", "99.5", "--time", "9:45 AM", "--date", "2026-11-23",
                                         "--group", "cas", "--now", "2026-11-23 10:15", "--config", cfg,
                                         "--write", plan]), 0)
            self.assertEqual(size.main(["--plan", plan, "--config", cfg, "--now", "2026-11-23 10:15",
                                        "--write", sizing]), 0)
            code = risk.main(["--sizing", sizing, "--config", cfg, "--now", "2026-11-23 10:20", "--out", verdict])
        self.assertEqual(code, 0)
        with open(sizing) as fh:
            sized = json.load(fh)
        with open(verdict) as fh:
            doc = json.load(fh)
        self.assertEqual((sized["qty"], sized["group"], sized["costs"]["loss_at_stop"]), (276, "cas", 499.1))
        loss = [c for c in doc["checks"] if c["check"] == "loss"][0]["detail"]
        self.assertIn("Rs 499.10 all-in", loss)           # the check re-derived exactly the sizing's figure
        self.assertEqual(doc["verdict"], "PASS")
        self.assertEqual(doc["pick"]["group"], "cas")


if __name__ == "__main__":
    unittest.main()
