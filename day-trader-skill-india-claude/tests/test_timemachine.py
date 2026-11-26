import datetime
import json
import os
import sys
import unittest

from _load import ROOT

sys.path.insert(0, os.path.join(ROOT, "trader-timemachine"))
import timemachine as tm  # noqa: E402
sys.path.insert(0, ROOT)
from common import in_market as market  # noqa: E402


class IndiaPlanTests(unittest.TestCase):
    def raw_plan(self, group="cas", **overrides):
        raw = {
            "symbol": "RELIANCE.NS", "side": "buy", "date": "2026-11-26", "group": group,
            "entry": 1480, "stop": 1465, "target": 1510, "entry_time": "10:15 AM",
            "flat_by": "3:05 PM", "setup": "orb", "opening_range": {"minutes": 15},
            "stop_rule": "auto", "interval": "5m",
        }
        raw.update(overrides)
        return raw

    def test_group_specific_session_lengths(self):
        cas = tm.normalize_plan(self.raw_plan("cas"))
        other = tm.normalize_plan(self.raw_plan("other"))
        self.assertEqual((cas["session_open"], cas["session_close"], cas["session_bars"]), ("09:15", "15:15", 72))
        self.assertEqual((other["session_open"], other["session_close"], other["session_bars"]), ("09:15", "15:30", 75))

    def test_entry_time_is_mapped_to_indian_bar_index(self):
        plan = tm.normalize_plan(self.raw_plan())
        self.assertEqual(plan["entry_bar"], 11)
        self.assertEqual(plan["entry_time"], "10:15 AM")

    def test_flat_by_is_parsed_in_ist(self):
        self.assertEqual(tm.parse_clock("3:05 PM", "flat_by"), 905)
        self.assertEqual(tm.parse_clock("15:05", "flat_by"), 905)

    def test_rejects_plan_without_calendar_coverage(self):
        with self.assertRaisesRegex(tm.TMError, "calendar is not available"):
            tm.normalize_plan(self.raw_plan(date="2027-01-04"))

    def test_nse_yahoo_symbol_is_preserved(self):
        self.assertIn("RELIANCE.NS", tm.chart_url("RELIANCE.NS"))
        self.assertEqual(tm.normalize_plan(self.raw_plan())["symbol"], "RELIANCE.NS")


class IndiaBarsTests(unittest.TestCase):
    def fixture(self, day=datetime.date(2026, 11, 25), group="cas"):
        op, cl = market.session(day, group)
        start = datetime.datetime.combine(day, op)
        stamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
        count = (cl.hour * 60 + cl.minute - op.hour * 60 - op.minute) // 5
        for i in range(count):
            local = start + datetime.timedelta(minutes=5 * i)
            utc = market.ist_to_utc(local)
            stamps.append(int((utc - datetime.datetime(1970, 1, 1)).total_seconds()))
            price = 100 + i * 0.01
            opens.append(price)
            highs.append(price + 0.2)
            lows.append(price - 0.2)
            closes.append(price + 0.05)
            volumes.append(1000)
        result = {
            "meta": {"symbol": "RELIANCE.NS", "dataGranularity": "5m", "regularMarketTime": stamps[-1] + 300},
            "timestamp": stamps,
            "indicators": {"quote": [{"open": opens, "high": highs, "low": lows,
                                        "close": closes, "volume": volumes}]},
        }
        now = market.ist_to_utc(datetime.datetime.combine(day, datetime.time(16, 0)))
        return result, now

    def test_parser_keeps_72_cas_bars_in_ist(self):
        result, now = self.fixture(group="cas")
        _, bars, dropped = tm.parse_bars(result, now, "cas")
        self.assertEqual(len(bars), 72)
        self.assertEqual((bars[0]["start"], bars[0]["slot"]), ("09:15", 0))
        self.assertEqual((bars[-1]["start"], bars[-1]["slot"]), ("15:10", 71))
        self.assertEqual(dropped["not_complete"], 0)

    def test_parser_keeps_75_other_group_bars(self):
        result, now = self.fixture(group="other")
        _, bars, _ = tm.parse_bars(result, now, "other")
        self.assertEqual(len(bars), 75)
        self.assertEqual(bars[-1]["start"], "15:25")

    def test_incomplete_bar_is_dropped(self):
        result, now = self.fixture()
        result["timestamp"].append(result["timestamp"][-1] + 300)
        for key, value in (("open", 100), ("high", 101), ("low", 99), ("close", 100), ("volume", 10)):
            result["indicators"]["quote"][0][key].append(value)
        now = market.ist_to_utc(datetime.datetime(2026, 11, 25, 15, 10))
        _, bars, dropped = tm.parse_bars(result, now, "cas")
        self.assertEqual(len(bars), 71)
        self.assertEqual(dropped["not_complete"], 1)


class IndiaRunTests(unittest.TestCase):
    def test_end_to_end_uses_plan_flat_by(self):
        plan = tm.normalize_plan({
            "symbol": "RELIANCE.NS", "side": "buy", "date": "2026-11-26", "group": "cas",
            "entry": 100.12, "stop": 99.5, "target": 101.0, "entry_time": "10:15 AM",
            "flat_by": "3:05 PM", "setup": "orb", "opening_range": {"minutes": 15},
            "stop_rule": "auto", "interval": "5m",
        })
        stamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
        day = datetime.date(2026, 10, 1)
        while day <= datetime.date(2026, 11, 26):
            if market.trading_day(day)[0]:
                span = market.session(day, "cas")
                if span:
                    start = datetime.datetime.combine(day, span[0])
                    count = (span[1].hour * 60 + span[1].minute - span[0].hour * 60 - span[0].minute) // 5
                    for i in range(count):
                        utc = market.ist_to_utc(start + datetime.timedelta(minutes=5 * i))
                        stamps.append(int((utc - datetime.datetime(1970, 1, 1)).total_seconds()))
                        price = 100 + (i % 9) * 0.03
                        opens.append(price); highs.append(price + 0.35); lows.append(price - 0.35)
                        closes.append(price + 0.02); volumes.append(5000)
            day += datetime.timedelta(days=1)
        result = {"chart": {"result": [{
            "meta": {"symbol": "RELIANCE.NS", "dataGranularity": "5m", "regularMarketTime": stamps[-1] + 300},
            "timestamp": stamps,
            "indicators": {"quote": [{"open": opens, "high": highs, "low": lows,
                                        "close": closes, "volume": volumes}]},
        }], "error": None}}
        now = market.ist_to_utc(datetime.datetime(2026, 11, 26, 16, 0))
        doc = tm.time_machine(json.dumps(result), plan, now, replays=100, seed=19, window=2)
        self.assertEqual(doc["mc"]["until"], "15:05")
        self.assertGreaterEqual(doc["mc"]["pool_sessions"], 20)
        self.assertTrue(doc["data"]["last_bar"].endswith("IST"))


if __name__ == "__main__":
    unittest.main()
