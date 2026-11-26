"""trader-budget: sizing one pick. Charge RATES are placeholders; the tests check the sizing logic."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal as Dc

from _load import load

size = load("trader-budget", "size")
D = datetime.date

PLAN = {"created_by": "trader-exit", "symbol": "AAA", "side": "buy", "date": "2026-11-23", "setup": "orb",
        "entry": 102.0, "entry_time": "9:45 AM", "stop": 100.4, "target": 105.2, "tick": 0.05,
        "stop_label": "opening-range midpoint", "target_label": "2R (two times the risk)", "flat_by": "15:05",
        "group": "cas"}
CFG = {"budget": 100000, "max_loss_per_trade_inr": 500}
TODAY = "2026-11-23"


def free_charges():
    cfg = size.costs.load()
    cfg = dict(cfg, brokerage={"flat": 0.0, "pct": 0.0}, stt_pct_sell=0, exchange_txn_pct=0, sebi_per_crore=0,
               stamp_pct_buy=0, gst_pct=0, verified=True)
    return cfg


def go(plan=None, cfg=None, **kw):
    p = dict(PLAN)
    p.update(plan or {})
    c = dict(CFG)
    c.update(cfg or {})
    kw.setdefault("today", TODAY)
    return size.size_trade(p, c, **kw)


def jload(path):
    with open(path) as fh:
        return json.load(fh)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


class Quantity(unittest.TestCase):
    def test_with_no_costs_it_is_the_plain_division(self):
        s = go(charges=free_charges(), slippage_pct=0)
        self.assertEqual(s["qty"], 312)                      # 500 / 1.60 = 312.5
        self.assertEqual((s["bound_by"], s["price_loss_at_stop"]), ("max loss", 499.2))

    def test_charges_and_slippage_shrink_it(self):
        s = go()
        self.assertEqual((s["qty"], s["bound_by"]), (276, "max loss"))
        c = s["costs"]
        self.assertLessEqual(c["loss_at_stop"], 500.0)
        self.assertEqual(c["loss_at_stop"], 499.1)
        over = size.costs.net_outcomes(Dc("102.0"), Dc("100.4"), Dc("105.2"), 277, 0.05)["loss_at_stop"]
        self.assertGreater(over, Dc("500"))                  # one more share would break the limit

    def test_the_search_matches_a_brute_force_scan(self):
        ch = size.costs.load()
        for max_loss in (50, 137.5, 500, 2500):
            want = max(q for q in range(0, 4000)
                       if q == 0 or float(size.costs.net_outcomes(Dc("102.0"), Dc("100.4"), Dc("105.2"), q, 0.05,
                                                                  ch)["loss_at_stop"]) <= max_loss)
            self.assertEqual(size.max_shares_for_loss(102.0, 100.4, 105.2, max_loss, 0.05, ch), want, max_loss)

    def test_other_limits_can_bind(self):
        s = go(cfg={"budget": 20000})                        # cap 15,000 / 102 = 147 shares
        self.assertEqual((s["qty"], s["bound_by"]), (147, "position cap"))
        s = go(cfg={"budget": 20000, "max_position_pct": 100, "max_order_inr": 50000})
        self.assertEqual((s["qty"], s["bound_by"]), (176, "cash"))                  # 18,000 / 102
        s = go(cfg={"max_order_inr": 10200})
        self.assertEqual((s["qty"], s["bound_by"]), (100, "max order"))

    def test_limits_list_reads_in_rupees(self):
        s = go()
        names = [l["name"] for l in s["limits"]]
        self.assertEqual(names, ["max loss", "position cap", "max order", "cash"])
        self.assertIn("Rs 500 all-in", s["limits"][0]["how"])
        self.assertEqual(s["limits"][1]["how"], "75% of Rs 1,00,000 = Rs 75,000 / 102.00")
        self.assertEqual(s["limits"][3]["how"], "budget Rs 1,00,000 minus the Rs 10,000 cash buffer = Rs 90,000 / 102.00")

    def test_shares_for_is_exact_in_paise(self):
        self.assertEqual(size.shares_for(75000, 102.0), 735)
        self.assertEqual(size.shares_for(0.3, 0.1), 3)       # floats would say 2
        self.assertEqual(size.shares_for(0, 10), 0)
        self.assertEqual(size.shares_for(100, 0), 0)


class Refusals(unittest.TestCase):
    def test_a_stop_too_far_for_the_max_loss(self):
        s = go(cfg={"max_loss_per_trade_inr": 1})
        self.assertEqual(s["qty"], 0)
        self.assertIn("one share risks Rs 1.60 before costs", s["refusal"])

    def test_charges_can_push_even_one_share_over(self):
        s = go(cfg={"max_loss_per_trade_inr": 1.75})         # 1 share: 1.60 plus slippage and charges
        self.assertEqual(s["qty"], 0)
        self.assertIn("charges and slippage on top push even one share past your Rs 1.75 max loss", s["refusal"])
        s = go(cfg={"max_loss_per_trade_inr": 2.0})
        self.assertEqual(s["qty"], 1)

    def test_reward_risk_under_the_minimum(self):
        s = go(cfg={"min_reward_risk": 2.5})
        self.assertIn("reward:risk 2.00", s["refusal"])
        self.assertIn("under your minimum 2.5", s["refusal"])

    def test_minimum_order(self):
        s = go(cfg={"min_order_inr": 50000})
        self.assertIn("276 shares of AAA is Rs 28,152, under your Rs 50,000 minimum order", s["refusal"])

    def test_one_share_costs_more_than_a_limit(self):
        s = go(cfg={"budget": 100})                           # 75% cap = Rs 75, under one share at Rs 102
        self.assertEqual(s["qty"], 0)
        self.assertIn("one share of AAA costs Rs 102, more than your position cap limit", s["refusal"])

    def test_hard_errors(self):
        for plan, cfg, kw, why in (
                ({"side": "sell"}, {}, {}, "long (buy) day trades only"),
                ({"date": "2026-11-20"}, {}, {}, "the plan is for 2026-11-20 and today is 2026-11-23 in India"),
                ({"symbol": ""}, {}, {}, "no symbol"),
                ({"stop": None}, {}, {}, "positive stop"),
                ({}, {"blocked": ["aaa"]}, {}, "on your blocked list"),
                ({}, {"budget": None}, {}, "no budget"),
                ({}, {"cash_buffer_pct": 100}, {}, "cash_buffer_pct"),
                ({}, {"slippage_pct": 9}, {}, "slippage_pct"),
                ({"stop": 103.0}, {}, {}, "is not below the entry"),
                ({"target": 101.0}, {}, {}, "the move has already happened")):
            with self.assertRaises(size.SizeError) as cm:
                go(plan, cfg, **kw)
            self.assertIn(why, str(cm.exception))


class EntryPrice(unittest.TestCase):
    def test_at_sizes_at_your_price_on_the_tick(self):
        s = go(at=102.02)
        self.assertEqual((s["entry"], s["entry_from"]), (102.0, "at"))
        s = go(at=103.0)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (103.0, "at", 2.6))
        self.assertLess(s["qty"], go()["qty"])

    def test_a_higher_fresh_price_is_used_a_lower_one_is_not(self):
        self.assertEqual(go(price=103.0)["entry_from"], "price")
        self.assertEqual(go(price=101.0)["entry_from"], "plan")
        self.assertEqual(go(price=102.0)["entry_from"], "plan")

    def test_fresh_price_rules(self):
        now = datetime.datetime(2026, 11, 23, 4, 45)
        doc = {"prices": {"AAA": 103.5}, "detail": {"AAA": {"time": "2026-11-23T04:40:00Z"}}}
        self.assertEqual(size.fresh_price(doc, "AAA", now), (103.5, datetime.datetime(2026, 11, 23, 4, 40), True))
        old = {"prices": {"AAA": 103.5}, "detail": {"AAA": {"time": "2026-11-23T04:20:00Z"}}}
        self.assertFalse(size.fresh_price(old, "AAA", now)[2])
        self.assertEqual(size.fresh_price({"prices": {}}, "AAA", now), (None, None, False))
        self.assertEqual(size.fresh_price({"prices": {"AAA": 100}}, "AAA", now)[2], False)   # undated


class Doc(unittest.TestCase):
    def test_the_sizing_file(self):
        s = go()
        d = size.sizing_doc(s, datetime.datetime(2026, 11, 23, 4, 45), "trader/plan.json (AAA orb 2026-11-23)")
        for k, v in (("created_by", "trader-budget"), ("schema_version", 2), ("exchange", "NSE"), ("currency", "INR"),
                     ("timezone", "Asia/Kolkata"), ("symbol", "AAA"), ("group", "cas"), ("side", "buy"),
                     ("entry", 102.0), ("stop", 100.4), ("target", 105.2), ("qty", 276), ("tick", 0.05),
                     ("created_at", "2026-11-23T04:45:00Z"), ("created_at_ist", "2026-11-23 10:15:00"),
                     ("flat_by", "15:05"), ("bound_by", "max loss")):
            self.assertEqual(d[k], v, k)
        self.assertIn("Nothing was placed", d["note"])
        self.assertNotIn("order_class", d)
        self.assertEqual(d["costs"]["charges_verified"], False)
        self.assertEqual(d["costs"]["cost_share_of_reward_pct"], 6.7)
        self.assertEqual(d["costs"]["net_reward_risk"], 1.65)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plan = os.path.join(self.tmp, "plan.json")
        self.cfg = os.path.join(self.tmp, "config.json")
        for p, doc in ((self.plan, PLAN), (self.cfg, CFG)):
            with open(p, "w") as fh:
                json.dump(doc, fh)

    def args(self, *extra):
        return ["--plan", self.plan, "--config", self.cfg, "--now", "2026-11-23 10:15"] + list(extra)

    def test_the_report(self):
        code, out, _ = run(self.args())
        self.assertEqual(code, 0)
        for want in ("AAA buy, opening-range breakout, entry 102.00 at 9:45 AM",
                     "Stop 100.40: opening-range midpoint. Target 105.20: 2R (two times the risk).",
                     "Size: 276 shares of AAA = Rs 28,152 at Rs 102.00.",
                     "If the stop is hit you lose Rs 499.10 all-in: Rs 441.60 of price move + Rs 57.50 of charges and slippage.",
                     "Reward:risk left after costs: 1.65 (it was 2.00 before). Costs and slippage take 6.7% of the reward.",
                     "circuit limit", "placeholders", "Flat by 15:05 IST", "Nothing was placed: this tool only sizes.",
                     "Education and tooling, not financial advice."):
            self.assertIn(want, out)
        self.assertNotIn("bracket", out)

    def test_write_only_when_sized(self):
        out = os.path.join(self.tmp, "sizing.json")
        code, text, _ = run(self.args("--write", out))
        self.assertEqual(code, 0)
        self.assertEqual(jload(out)["qty"], 276)
        self.assertIn("Wrote the sizing to", text)
        os.remove(out)
        code, text, _ = run(self.args("--write", out, "--budget", "100", "--charges", "/nonexistent"))
        self.assertEqual(code, 1)
        code, text, _ = run(["--plan", self.plan, "--config", self.cfg, "--now", "2026-11-23 10:15", "--write", out,
                             "--budget", "150"])
        self.assertEqual(code, 1)
        self.assertIn("Not sized:", text)
        self.assertFalse(os.path.exists(out))

    def test_json_and_flags(self):
        code, out, _ = run(self.args("--json", "--at", "103", "--slippage-pct", "0"))
        doc = json.loads(out)
        self.assertEqual((doc["sizing"]["entry"], doc["sizing"]["entry_from"]), (103.0, "at"))
        self.assertEqual(doc["sizing"]["costs"]["slippage_pct"], 0.0)

    def test_your_verified_charges(self):
        mine = {k: v for k, v in free_charges().items() if not k.startswith("_")}
        path = os.path.join(self.tmp, "charges.json")
        with open(path, "w") as fh:
            json.dump(mine, fh)
        code, out, _ = run(self.args("--charges", path, "--slippage-pct", "0"))
        self.assertIn("Size: 312 shares", out)
        self.assertNotIn("placeholders", out)

    def test_bad_inputs_are_plain_errors(self):
        code, _, err = run(["--plan", os.path.join(self.tmp, "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("no plan (run trader-exit first)", err)
        code, _, err = run(self.args("--at", "cheap"))
        self.assertIn("--at wants a price", err)
        code, _, err = run(self.args("--charges", os.path.join(self.tmp, "nope.json")))
        self.assertIn("could not read the charges file", err)
        code, _, err = run(["--plan", self.plan, "--config", self.cfg, "--now", "2026-11-24 10:15"])
        self.assertIn("the plan is for 2026-11-23 and today is 2026-11-24", err)

    def test_a_fresh_higher_price_note(self):
        prices = os.path.join(self.tmp, "prices.json")
        with open(prices, "w") as fh:
            json.dump({"prices": {"AAA": 103.0}, "detail": {"AAA": {"time": "2026-11-23T04:44:00Z"}}}, fh)
        code, out, _ = run(self.args("--prices", prices))
        self.assertIn("AAA last Rs 103.00 (as of 10:14 IST) is above the plan's entry 102.00", out)
        self.assertIn("Sized at Rs 103.00 (the latest price, above the plan's entry)", out)
        with open(prices, "w") as fh:
            json.dump({"prices": {"AAA": 103.0}, "detail": {"AAA": {"time": "2026-11-23T04:00:00Z"}}}, fh)
        code, out, _ = run(self.args("--prices", prices))
        self.assertIn("not used (older than 15 minutes)", out)


class Time(unittest.TestCase):
    def test_parse_now_is_indian_time(self):
        self.assertEqual(size.parse_now("2026-11-23 10:15"), datetime.datetime(2026, 11, 23, 4, 45))
        with self.assertRaises(size.SizeError):
            size.parse_now("tomorrow")

    def test_money_formats(self):
        self.assertEqual(size.money(28152), "Rs 28,152")
        self.assertEqual(size.money(499.1), "Rs 499.10")
        self.assertEqual(size.money(100000), "Rs 1,00,000")


class Config(unittest.TestCase):
    def test_the_shipped_example_is_valid_and_pick_only(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        for k in ("budget", "max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day", "min_reward_risk",
                  "max_cost_pct_of_reward", "slippage_pct"):
            self.assertIn(k, cfg, k)
        for k in ("broker", "paper", "max_order_usd", "pdt_guard", "sec_user_agent"):
            self.assertNotIn(k, cfg, k)
        self.assertIs(cfg["allow_short"], False)
        self.assertIs(cfg["allow_margin"], False)
        s = size.size_trade(dict(PLAN), cfg, today=TODAY)
        self.assertGreater(s["qty"], 0)


if __name__ == "__main__":
    unittest.main()
