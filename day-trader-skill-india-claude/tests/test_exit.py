"""trader-exit on NSE prices. Charts are SYNTHETIC (tests/_india.py); charge rates are placeholders."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal as Dc

import _india as I
from _load import load

exit_ = load("trader-exit", "exit")
entry = load("trader-entry", "entry")
D = datetime.date
DAY = D(2026, 11, 23)
HEAD = [100.0, 101.0, 100.5, 100.8, 101.2, 102.0, 102.4, 102.1, 102.3]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = exit_.main(argv)
    return code, out.getvalue(), err.getvalue()


def jload(path):
    with open(path) as fh:
        return json.load(fh)


def calc(entry_price=102.0, stop="or-mid", target="2R", oh=101.3, ol=99.5, **kw):
    sr, sp = exit_.parse_stop(stop)
    tr, tp = exit_.parse_target(target)
    return exit_.compute(entry_price, sr, sp, tr, tp, oh, ol, **kw)


class Compute(unittest.TestCase):
    def test_or_mid_stop_and_2r_target(self):
        c = calc()
        self.assertEqual((c["entry"], c["stop"], c["risk"], c["target"], c["reward_risk"]),
                         (Dc("102.00"), Dc("100.40"), Dc("1.60"), Dc("105.20"), Dc("2.00")))
        self.assertEqual(c["stop_label"], "opening-range midpoint")
        self.assertEqual(c["stop_detail"], "(Rs 101.30 + Rs 99.50) / 2 = Rs 100.4")

    def test_a_stop_rounds_down_to_the_tick_away_from_the_entry(self):
        c = calc(oh=101.35, ol=99.5)                     # midpoint 100.425
        self.assertEqual(c["stop"], Dc("100.40"))
        self.assertIn("rounded down to the Rs 0.05 tick", c["stop_detail"])

    def test_the_entry_and_range_round_to_the_nearest_tick(self):
        c = calc(entry_price=102.02, oh=101.32, ol=99.47)
        self.assertEqual(c["entry"], Dc("102.00"))
        self.assertEqual(c["stop"], Dc("100.35"))        # (101.30 + 99.45) / 2 = 100.375, floored to the tick

    def test_target_rounds_half_up_to_the_tick(self):
        c = calc(entry_price=100.0, stop="price:99.25", target="1.5R")    # risk 0.75, target 101.125
        self.assertEqual(c["target"], Dc("101.15"))
        c = calc(entry_price=100.0, stop="price:99", target="price:105.23")
        self.assertEqual((c["target"], c["target_label"]), (Dc("105.25"), "your price (5.25R)"))

    def test_pct_stop(self):
        c = calc(entry_price=200.0, stop="pct:1", target="1R", oh=None, ol=None)
        self.assertEqual((c["stop"], c["risk"], c["target"]), (Dc("198.00"), Dc("2.00"), Dc("202.00")))
        self.assertEqual(c["stop_label"], "1% under the entry")

    def test_or_low_stop(self):
        self.assertEqual(calc(stop="or-low")["stop"], Dc("99.50"))

    def test_another_tick(self):
        c = calc(entry_price=50.0, stop="price:49.37", target="1R", oh=None, ol=None, tick=0.01)
        self.assertEqual((c["stop"], c["target"]), (Dc("49.37"), Dc("50.63")))

    def test_refusals(self):
        for kwargs, why in (
                (dict(stop="price:102"), "at or above the entry"),
                (dict(stop="price:103"), "at or above the entry"),
                (dict(stop="price:101.95"), "less than the minimum Rs 0.10"),
                (dict(target="price:102"), "at or below the entry"),
                (dict(oh=99.0, ol=99.0), "must be above its low")):
            with self.assertRaises(exit_.PlanError) as cm:
                calc(**kwargs)
            self.assertIn(why, str(cm.exception))

    def test_minimum_risk_is_two_ticks_or_a_twentieth_of_a_percent(self):
        calc(entry_price=100.0, stop="price:99.9", target="2R", oh=None, ol=None)        # exactly 2 ticks: allowed
        with self.assertRaises(exit_.PlanError) as cm:
            calc(entry_price=100.0, stop="price:99.95", oh=None, ol=None)
        self.assertIn("two ticks, or 0.05% of the price", str(cm.exception))
        with self.assertRaises(exit_.PlanError):                                          # 0.05% of 4000 is Rs 2
            calc(entry_price=4000.0, stop="price:3998.5", oh=None, ol=None)
        calc(entry_price=4000.0, stop="price:3998", target="2R", oh=None, ol=None)

    def test_a_missing_range_is_named(self):
        with self.assertRaises(exit_.PlanError) as cm:
            calc(oh=None, ol=None)
        self.assertIn("needs the opening range", str(cm.exception))

    def test_atr_stop(self):
        c = calc(stop="atr:1.5", atr=1.1333333, atr_bars=6, oh=None, ol=None)
        self.assertEqual(c["stop"], Dc("100.30"))
        self.assertIn("(fewer than 14 so far)", c["stop_detail"])


class Parsing(unittest.TestCase):
    def test_amounts_accept_rupee_forms(self):
        self.assertEqual(exit_.D("Rs 1,234.50"), Dc("1234.50"))
        self.assertEqual(exit_.D("\u20b9100"), Dc("100"))
        self.assertEqual(exit_.D("rs.99.5"), Dc("99.5"))
        with self.assertRaises(exit_.PlanError):
            exit_.D("abc")
        self.assertEqual(exit_.parse_stop("price:Rs 99.5"), ("price", Dc("99.5")))
        self.assertEqual(exit_.parse_target("price:\u20b9105"), ("price", Dc("105")))

    def test_bad_rules(self):
        for bad in ("tight", "pct:0", "pct:100", "atr:", "price:"):
            with self.assertRaises(exit_.PlanError):
                exit_.parse_stop(bad)
        for bad in ("0R", "big", "price:"):
            with self.assertRaises(exit_.PlanError):
                exit_.parse_target(bad)
        with self.assertRaises(exit_.PlanError):
            exit_.parse_trail("atr:2")

    def test_labels_and_money(self):
        self.assertEqual(exit_.r_label(Dc("2")), "2R (two times the risk)")
        self.assertEqual(exit_.r_label(Dc("1.5")), "1.5R (1.5 times the risk)")
        self.assertEqual(exit_.ratio(Dc("2")), "2.0")
        self.assertEqual(exit_.ratio(Dc("1.37")), "1.37")
        self.assertEqual(exit_.money(Dc("123456.5")), "Rs 1,23,456.50")

    def test_parse_now_is_indian_time(self):
        self.assertEqual(exit_.parse_now("2026-11-23 10:15"), datetime.datetime(2026, 11, 23, 4, 45))
        self.assertEqual(exit_.parse_now("2026-11-23 10:15 IST"), datetime.datetime(2026, 11, 23, 4, 45))
        with self.assertRaises(exit_.PlanError):
            exit_.parse_now("later")

    def test_clock(self):
        self.assertEqual(exit_.parse_clock("9:45 AM", "t"), datetime.time(9, 45))
        self.assertEqual(exit_.parse_clock("3:05 PM", "t"), datetime.time(15, 5))
        with self.assertRaises(exit_.PlanError):
            exit_.parse_clock("13:00 PM", "t")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._get = exit_.yahoo_get
        exit_.yahoo_get = lambda *a, **k: self.fail("the network was touched")
        self.addCleanup(setattr, exit_, "yahoo_get", self._get)

    def p(self, name):
        return os.path.join(self.tmp, name)

    def put(self, name, doc):
        with open(self.p(name), "w") as fh:
            json.dump(doc, fh)

    def entry_json(self, **kw):
        doc = {"created_by": "trader-entry", "symbol": "AAA", "setup": "orb", "side": "buy", "date": "2026-11-23",
               "status": "triggered", "time": "9:45 AM", "bar_index": 5, "price": 102.0, "interval": "5m",
               "opening_range": {"high": 101.3, "low": 99.5, "minutes": 15}, "group": "any"}
        doc.update(kw)
        self.put("entry.json", doc)
        return self.p("entry.json")

    def plan(self, *extra, **kw):
        path = self.entry_json(**kw)
        return run(["plan", "--from", path, "--now", "2026-11-23 10:15", "--config", self.p("none.json")] + list(extra))


class FromEntry(Fixture):
    def test_the_default_plan(self):
        code, out, _ = self.plan("--json")
        doc = json.loads(out)
        for k, v in (("created_by", "trader-exit"), ("schema_version", 2), ("symbol", "AAA"), ("exchange", "NSE"),
                     ("currency", "INR"), ("timezone", "Asia/Kolkata"), ("group", "any"), ("side", "buy"),
                     ("entry", 102.0), ("stop", 100.4), ("target", 105.2), ("risk_per_share", 1.6),
                     ("reward_risk", 2.0), ("tick", 0.05), ("flat_by", "15:05"), ("entry_time", "9:45 AM"),
                     ("stop_rule", "or_mid"), ("target_label", "2R (two times the risk)")):
            self.assertEqual(doc[k], v, k)
        self.assertEqual(doc["opening_range"], {"high": 101.3, "low": 99.5, "minutes": 15})
        self.assertNotIn("costs", doc)
        self.assertNotIn("stop_status", doc)

    def test_the_words(self):
        code, out, _ = self.plan()
        self.assertEqual(code, 0)
        for want in ("Exit plan: AAA long, Mon 2026-11-23, opening-range breakout on 5-minute bars",
                     "Entry         Rs 102.00 at 9:45 AM (bar 5)",
                     "Stop          Rs 100.40  opening-range midpoint: (Rs 101.30 + Rs 99.50) / 2 = Rs 100.4",
                     "Target        Rs 105.20  2R (two times the risk)",
                     "Risk a share  Rs 1.60 (1.57% of the entry)", "Reward:risk   2.0",
                     "Flat by       15:05 IST, win or lose: no overnight hold",
                     "circuit limit", "Nothing was placed."):
            self.assertIn(want, out)

    def test_write_and_show(self):
        out = self.p("plan.json")
        self.plan("--write", out)
        self.assertEqual(jload(out)["stop"], 100.4)
        code, text, _ = run(["show", "--plan", out, "--now", "2026-11-23 11:00"])
        self.assertEqual(code, 0)
        self.assertIn("Stop          Rs 100.40", text)
        code, text, _ = run(["show", "--plan", out, "--now", "2026-11-25 11:00"])
        self.assertIn("this plan is for 2026-11-23, not today (2026-11-25)", text)

    def test_show_without_a_plan(self):
        code, out, _ = run(["show", "--plan", self.p("nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("No plan at", out)

    def test_a_trigger_that_has_not_happened_is_refused_unless_planning_ahead(self):
        code, _, err = self.plan(status="waiting")
        self.assertEqual(code, 1)
        self.assertIn("is waiting, not triggered", err)
        path = self.entry_json(status="waiting")
        code, out, _ = run(["plan", "--from", path, "--entry", "103.5", "--now", "2026-11-23 10:15",
                            "--config", self.p("none.json"), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["entry"], 103.5)

    def test_a_short_entry_is_refused(self):
        code, _, err = self.plan(side="sell")
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)
        code, _, err = run(["plan", "--symbol", "AAA", "--entry", "100", "--side", "short"])
        self.assertIn("Longs only", err)

    def test_level_setup_needs_a_stop(self):
        code, _, err = run(["plan", "--symbol", "TCS", "--entry", "4100", "--setup", "level", "--now",
                            "2026-11-23 10:15", "--config", self.p("none.json")])
        self.assertEqual(code, 1)
        self.assertIn("has no opening-range stop to default to", err)
        code, out, _ = run(["plan", "--symbol", "TCS", "--entry", "4100", "--setup", "level", "--stop", "pct:1",
                            "--now", "2026-11-23 10:15", "--config", self.p("none.json"), "--json"])
        doc = json.loads(out)
        self.assertEqual((doc["stop"], doc["target"]), (4059.0, 4182.0))

    def test_symbol_forms_and_flag_conflicts(self):
        code, out, _ = run(["plan", "--symbol", "reliance.ns", "--entry", "1450.5", "--or-high", "1448",
                            "--or-low", "1432.5", "--now", "2026-11-23 10:15", "--config", self.p("none.json"),
                            "--json"])
        self.assertEqual(json.loads(out)["symbol"], "RELIANCE")
        code, _, err = run(["plan", "--symbol", "AAA", "--from", self.entry_json()])
        self.assertIn("use --from or --symbol, not both", err)
        code, _, err = run(["plan", "--symbol", "AAA"])
        self.assertIn("--symbol needs --entry", err)
        code, _, err = run(["plan", "--symbol", "AAA", "--entry", "100", "--or-high", "101"])
        self.assertIn("give both --or-high and --or-low", err)


class FlatBy(Fixture):
    def flat(self, *extra, **kw):
        code, out, err = self.plan("--json", *extra, **kw)
        return code, (json.loads(out) if code == 0 else None), err

    def test_default_follows_the_group_and_the_date(self):
        self.assertEqual(self.flat(group="cas")[1]["flat_by"], "15:05")
        self.assertEqual(self.flat(group="other")[1]["flat_by"], "15:20")
        self.assertEqual(self.flat(group="any")[1]["flat_by"], "15:05")
        d = self.flat(group="cas", date="2026-12-07")[1]                  # new rule: runs to 15:30
        self.assertEqual(d["flat_by"], "15:20")

    def test_the_group_flag_overrides_the_entry_file_only_when_given(self):
        self.assertEqual(self.flat("--group", "other", group="cas")[1]["flat_by"], "15:20")

    def test_explicit_flat_by(self):
        self.assertEqual(self.flat("--flat-by", "14:30")[1]["flat_by"], "14:30")
        code, doc, err = self.flat("--flat-by", "15:20")
        self.assertEqual(code, 1)
        self.assertIn("at or after the 3:15 PM end of continuous trading", err)

    def test_a_config_flat_by_past_the_end_is_replaced_with_a_note(self):
        cfg = self.p("config.json")
        with open(cfg, "w") as fh:
            json.dump({"flat_by": "15:25"}, fh)
        path = self.entry_json()
        code, out, _ = run(["plan", "--from", path, "--now", "2026-11-23 10:15", "--config", cfg, "--json"])
        doc = json.loads(out)
        self.assertEqual(doc["flat_by"], "15:05")
        self.assertTrue(any("flat_by 15:25 in your config is at or after the 3:15 PM end" in w for w in doc["warnings"]))
        with open(cfg, "w") as fh:
            json.dump({"flat_by": "14:50"}, fh)
        code, out, _ = run(["plan", "--from", path, "--now", "2026-11-23 10:15", "--config", cfg, "--json"])
        self.assertEqual(json.loads(out)["flat_by"], "14:50")

    def test_an_entry_after_the_flat_by_is_refused(self):
        code, _, err = self.plan(time="3:10 PM")
        self.assertEqual(code, 1)
        self.assertIn("the entry at 3:10 PM is at or after your flat-by 15:05", err)

    def test_closed_days(self):
        code, _, err = self.plan(date="2026-11-24")
        self.assertEqual(code, 1)
        self.assertIn("the NSE is closed on 2026-11-24 (NSE holiday: Prakash Gurpurb", err)
        code, _, err = self.plan(date="2026-11-28")
        self.assertIn("(weekend)", err)

    def test_a_date_the_holiday_list_does_not_cover_gets_a_warning(self):
        code, doc, _ = self.flat(date="2027-01-20")
        self.assertEqual(code, 0)
        self.assertTrue(any("holiday list covers 2026 only" in w for w in doc["warnings"]))


class Costs(Fixture):
    def test_qty_adds_the_money_after_charges_and_slippage(self):
        code, out, _ = self.plan("--qty", "100", "--json")
        doc = json.loads(out)
        c = doc["costs"]
        want = exit_.costs.net_outcomes(Dc("102.00"), Dc("100.40"), Dc("105.20"), 100, 0.05)
        self.assertEqual((c["qty"], c["slippage_pct"]), (100, 0.05))
        self.assertEqual(c["loss_at_stop"], float(want["loss_at_stop"]))
        self.assertEqual(c["gain_at_target"], float(want["gain_at_target"]))
        self.assertEqual(c["net_reward_risk"], float(want["net_reward_risk"]))
        self.assertGreater(c["loss_at_stop"], 160.0)           # 100 x Rs 1.60 plus charges plus slippage
        self.assertLess(c["net_reward_risk"], 2.0)
        self.assertIs(c["charges_verified"], False)
        self.assertTrue(any("placeholders" in w for w in doc["warnings"]))

    def test_the_words_carry_the_money_lines(self):
        code, out, _ = self.plan("--qty", "100")
        for want in ("For 100 shares, after charges and 0.05% slippage on each fill:", "Stop hit    lose Rs ",
                     "Target hit  gain Rs ", "Reward:risk left 1.6"):
            self.assertIn(want, out)

    def test_your_own_verified_charges_remove_the_placeholder_warning(self):
        base = exit_.costs.load()
        mine = {k: v for k, v in base.items() if not k.startswith("_")}
        mine.update(verified=True, brokerage={"flat": 0.0, "pct": 0.0})
        self.put("charges.json", mine)
        code, out, _ = self.plan("--qty", "100", "--slippage-pct", "0", "--charges", self.p("charges.json"), "--json")
        doc = json.loads(out)
        self.assertIs(doc["costs"]["charges_verified"], True)
        self.assertFalse(any("placeholders" in w for w in doc["warnings"]))
        self.assertEqual(doc["costs"]["charges_file"], "charges.json")

    def test_a_thin_net_reward_risk_warns(self):
        cfg = self.p("config.json")
        with open(cfg, "w") as fh:
            json.dump({"min_reward_risk": 1.8}, fh)
        path = self.entry_json()
        code, out, _ = run(["plan", "--from", path, "--now", "2026-11-23 10:15", "--config", cfg, "--qty", "100",
                            "--json"])
        doc = json.loads(out)
        self.assertTrue(any("After charges and slippage the reward:risk is" in w for w in doc["warnings"]))

    def test_slippage_needs_a_quantity(self):
        code, _, err = self.plan("--slippage-pct", "0.1")
        self.assertEqual(code, 1)
        self.assertIn("--slippage-pct needs --qty", err)
        code, _, err = self.plan("--qty", "0")
        self.assertIn("--qty must be at least 1", err)
        code, _, err = self.plan("--qty", "10", "--slippage-pct", "9")
        self.assertIn("a percent from 0 to under 5", err)

    def test_a_bad_charges_file_is_a_plain_error(self):
        code, _, err = self.plan("--qty", "10", "--charges", self.p("missing.json"))
        self.assertEqual(code, 1)
        self.assertIn("could not read the charges file", err)


class Trail(Fixture):
    def test_trail_rule(self):
        code, out, _ = self.plan("--trail", "pct:1", "--json")
        t = json.loads(out)["trail"]
        self.assertEqual((t["rule"], t["percent"], t["starts_after_close_at"]), ("pct", 1.0, 103.6))
        self.assertIn("once a bar closes at or above Rs 103.60 (1R above the entry), keep the stop 1% under the "
                      "highest close, and never lower it", t["text"])
        code, out, _ = self.plan("--trail", "pct:1")
        self.assertIn("Trail         once a bar closes at or above Rs 103.60", out)

    def test_bad_trail(self):
        for bad in ("atr:2", "pct:0", "pct:100", "1"):
            code, _, err = self.plan("--trail", bad)
            self.assertEqual(code, 1, bad)


class Atr(Fixture):
    def setUp(self):
        super().setUp()
        self.put("AAA.json", I.v8("AAA.NS", {DAY: I.bars(I.pad(HEAD, 72), first_open=99.8)}))

    def test_atr_stop_on_the_sessions_bars(self):
        path = self.entry_json()
        code, out, _ = run(["plan", "--from", path, "--stop", "atr:1.5", "--offline", self.tmp,
                            "--now", "2026-11-23 10:15", "--config", self.p("none.json"), "--json"])
        doc = json.loads(out)
        # true ranges of bars 0-5: 0.8, 1.6, 1.1, 0.9, 1.0, 1.4 -> mean 1.1333; 102.00 - 1.5 x 1.1333 = 100.30
        self.assertEqual((doc["stop"], doc["atr_bars"], doc["atr"]), (100.3, 6, 1.1333))
        self.assertIn("(fewer than 14 so far)", doc["stop_detail"])

    def test_atr_without_enough_bars_or_without_a_file(self):
        path = self.entry_json(bar_index=1)
        code, _, err = run(["plan", "--from", path, "--stop", "atr:1.5", "--offline", self.tmp,
                            "--now", "2026-11-23 10:15", "--config", self.p("none.json")])
        self.assertEqual(code, 1)
        self.assertIn("needs at least 3 closed 5m bars of AAA on 2026-11-23 by the entry; found 2", err)
        code, _, err = run(["plan", "--from", self.entry_json(), "--stop", "atr:1", "--offline",
                            os.path.join(self.tmp, "nowhere"), "--now", "2026-11-23 10:15"])
        self.assertIn("no offline file", err)

    def test_the_auction_bars_are_not_used(self):
        chart = exit_.load_chart("AAA", "5m", DAY, self.tmp)
        bars = exit_.session_bars(chart, DAY, "5m", now_utc=exit_.parse_now("2026-11-23 15:40"))
        self.assertEqual(len(bars), 72)


class Pipeline(Fixture):
    def test_entry_check_feeds_exit_plan(self):
        self.put("AAA.json", I.v8("AAA.NS", {DAY: I.bars(I.pad(HEAD, 72), first_open=99.8)}))
        out = self.p("entry.json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = entry.main(["check", "AAA", "--now", "2026-11-23 10:15", "--offline", self.tmp, "--write", out])
        self.assertEqual(code, 0)
        code, text, _ = run(["plan", "--from", out, "--now", "2026-11-23 10:15", "--config", self.p("none.json"),
                             "--json"])
        doc = json.loads(text)
        self.assertEqual((doc["symbol"], doc["entry"], doc["stop"], doc["target"], doc["flat_by"], doc["group"]),
                         ("AAA", 102.0, 100.4, 105.2, "15:05", "any"))
        self.assertEqual(doc["opening_range"], {"high": 101.3, "low": 99.5, "minutes": 15})
        self.assertEqual(doc["entry_time"], "9:45 AM")


if __name__ == "__main__":
    unittest.main()
