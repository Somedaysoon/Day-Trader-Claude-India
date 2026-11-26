import datetime
import importlib.util
import json
import os
import tempfile
import unittest

from _load import ROOT

PATH = os.path.join(ROOT, "trader-alerts", "alerts.py")
spec = importlib.util.spec_from_file_location("trader_alerts_india", PATH)
alerts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alerts)


class IndiaClock(unittest.TestCase):
    def test_naive_now_is_ist_and_aware_now_is_absolute(self):
        self.assertEqual(alerts.parse_now("2026-11-26 10:00"), datetime.datetime(2026, 11, 26, 4, 30))
        self.assertEqual(alerts.parse_now("2026-11-26T04:30:00Z"), datetime.datetime(2026, 11, 26, 4, 30))
        self.assertEqual(alerts.to_ist(datetime.datetime(2026, 11, 26, 4, 30)), datetime.datetime(2026, 11, 26, 10, 0))

    def test_market_status_uses_shared_calendar_and_any_group(self):
        st = alerts.market_status(alerts.parse_now("2026-11-26 10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"]), ("09:15", "15:15"))
        self.assertEqual(st["now_ist"], "2026-11-26 10:00")

    def test_group_session_hours(self):
        now = alerts.parse_now("2026-11-26 15:20")
        self.assertFalse(alerts.market_status(now, "cas")["open"])
        self.assertTrue(alerts.market_status(now, "other")["open"])
        self.assertEqual(alerts.market_status(now, "other")["closes"], "15:30")

    def test_weekend_and_calendar_unknown_warning(self):
        sat = alerts.market_status(alerts.parse_now("2026-11-28 10:00"))
        self.assertFalse(sat["open"])
        self.assertEqual(sat["reason"], "weekend")
        unknown = alerts.market_status(alerts.parse_now("2028-03-01 10:00"))
        self.assertFalse(unknown["calendar_known"])
        self.assertIn("verify", unknown["calendar_warning"])


class IndiaSymbolsAndPlans(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertIn("/RELIANCE.NS?", alerts.yahoo_url("RELIANCE"))
        self.assertIn("/RELIANCE.NS?", alerts.yahoo_url("RELIANCE.NS"))
        self.assertIn("/ABC.BO?", alerts.yahoo_url("ABC.BO"))

    def test_plan_generates_stop_target_and_group_flat_by(self):
        plan = {"symbol": "RELIANCE", "date": "2026-11-26", "group": "other",
                "entry": 1480, "stop": 1450, "target": 1540, "flat_by": "15:20"}
        rules = alerts.rules_from_plan(plan)
        self.assertEqual([r["type"] for r in rules], ["stop", "target", "time"])
        self.assertEqual([r["group"] for r in rules], ["other"] * 3)
        self.assertEqual(rules[-1]["at"], "15:20")
        self.assertEqual(rules[-1]["date"], "2026-11-26")

    def test_plan_fallback_is_ten_minutes_before_group_close(self):
        plan = {"symbol": "RELIANCE", "date": "2026-11-26", "group": "other", "stop": 90, "target": 110}
        self.assertEqual(alerts.rules_from_plan(plan)[-1]["at"], "15:20")
        plan["group"] = "cas"
        self.assertEqual(alerts.rules_from_plan(plan)[-1]["at"], "15:05")


class SignalEvaluation(unittest.TestCase):
    def setUp(self):
        self.now = alerts.parse_now("2026-11-26 10:05")
        self.status = alerts.market_status(self.now)

    def test_stop_and_target_signals_are_rupee_signals(self):
        rules = [
            {"id": "s", "symbol": "RELIANCE", "type": "stop", "price": 100},
            {"id": "t", "symbol": "TCS", "type": "target", "price": 200},
        ]
        quotes = {"RELIANCE": {"price": 99}, "TCS": {"price": 201}}
        rings, state = alerts.evaluate(rules, quotes, self.status, {}, self.now)
        self.assertEqual([r["id"] for r in rings], ["s", "t"])
        self.assertIn("STOP HIT", rings[0]["message"])
        self.assertIn("₹99.00", rings[0]["message"])
        self.assertIn("TARGET HIT", rings[1]["message"])

    def test_time_alert_uses_group_close_and_ist(self):
        now = alerts.parse_now("2026-11-26 15:20")
        status = alerts.market_status(now)
        rule = {"id": "exit", "symbol": "RELIANCE", "type": "time", "at": "15:20",
                "label": "TIME TO EXIT", "group": "other", "before_close": True}
        rings, _ = alerts.evaluate([rule], {}, status, {}, now)
        self.assertEqual(len(rings), 1)
        self.assertIn("15:20 IST", rings[0]["message"])
        self.assertIn("act manually", rings[0]["message"])
        self.assertNotIn("broker", rings[0]["message"].lower())

    def test_stop_alert_only_rings_once_until_reset(self):
        rule = {"id": "s", "symbol": "RELIANCE", "type": "stop", "price": 100}
        quotes = {"RELIANCE": {"price": 99}}
        rings, state = alerts.evaluate([rule], quotes, self.status, {}, self.now)
        self.assertEqual(len(rings), 1)
        rings2, _ = alerts.evaluate([rule], quotes, self.status, state, self.now)
        self.assertEqual(rings2, [])

    def test_calendar_and_time_rule_once_per_ist_day(self):
        rule = {"id": "bell", "type": "opening_bell"}
        rings, state = alerts.evaluate([rule], {}, self.status, {}, self.now)
        self.assertEqual(len(rings), 1)
        rings2, _ = alerts.evaluate([rule], {}, self.status, state, self.now)
        self.assertEqual(rings2, [])


if __name__ == "__main__":
    unittest.main()
