"""trader-entry on NSE data. Every chart here is SYNTHETIC (tests/_india.py), not a real capture."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import _india as I
from _load import load

entry = load("trader-entry", "entry")
D = datetime.date
DAY = D(2026, 11, 23)
HEAD = [100.0, 101.0, 100.5, 100.8, 101.2, 102.0, 102.4, 102.1, 102.3]
# range bars 09:15-09:25: high 101.3, low 99.5; the 09:40 bar is the first to close above it (102.0)


def chart_of(bar_list=None, day=DAY, symbol="AAA", gran=None):
    doc = I.v8(symbol + ".NS", {day: bar_list if bar_list is not None else I.bars(I.pad(HEAD, 72), first_open=99.8)})
    if gran:
        doc["chart"]["result"][0]["meta"]["dataGranularity"] = gran
    return entry.parse_chart(json.dumps(doc), symbol)


def at(text, chart=None, **kw):
    return entry.evaluate(chart or chart_of(), entry.parse_now(text), **kw)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


def jload(path):
    with open(path) as fh:
        return json.load(fh)


class Phases(unittest.TestCase):
    def test_before_the_open(self):
        r = at("2026-11-23 08:59")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertEqual(r["reason"], "the NSE opens at 9:15 and the 15-minute range is set at 9:30")

    def test_the_range_is_still_forming(self):
        r = at("2026-11-23 09:27")
        self.assertEqual((r["phase"], r["bars_closed"]), ("forming", 2))
        self.assertEqual(r["reason"], "2 of 3 range bars closed; the range is set at 9:30")

    def test_range_set_at_0930_and_waiting_for_a_close_above_it(self):
        r = at("2026-11-23 09:30")
        self.assertEqual((r["status"], r["level"]), ("waiting", 101.3))
        self.assertEqual(r["opening_range"], {"high": 101.3, "low": 99.5, "minutes": 15, "bars": 3, "of": 3,
                                              "complete": True, "avg_volume": 10000})
        self.assertEqual(r["reason"], "last close Rs 100.50 (9:25 bar), Rs 0.80 under the range high Rs 101.30 (-0.79%)")
        r = at("2026-11-23 09:43")
        self.assertEqual(r["reason"], "last close Rs 101.20 (9:35 bar), Rs 0.10 under the range high Rs 101.30 (-0.10%)")

    def test_trigger_is_the_bar_close_not_a_wick(self):
        r = at("2026-11-23 10:15")
        t = r["trigger"]
        self.assertEqual((r["status"], t["time"], t["close_time"], t["bar_start"], t["bar_index"], t["price"]),
                         ("triggered", "9:45 AM", "09:45", "09:40", 5, 102.0))
        self.assertEqual((t["pct_over"], r["minutes_since"], r["level"]), (0.69, 30, 101.3))
        r = at("2026-11-23 09:44")                    # the 09:40 bar is still forming
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"], {"bar_start": "09:40", "closes": "9:45 AM", "price": 102.0})

    def test_a_wick_over_the_high_does_not_trigger(self):
        bl = I.bars(I.pad([100.0, 101.0, 100.5, 100.8, 101.2, 101.1, 101.0], 72), first_open=99.8)
        bl[5] = (101.2, 105.0, 100.9, 101.1, 10000)   # wicks to 105, closes under the 101.3 high
        r = at("2026-11-23 10:15", chart_of(bl))
        self.assertEqual(r["status"], "waiting")

    def test_pre_open_prints_before_0915_are_not_candles(self):
        doc = I.v8("AAA.NS", {DAY: I.bars(I.pad(HEAD, 72), first_open=99.8)})
        res = doc["chart"]["result"][0]
        pre = I.day_series(DAY, I.bars([90.0, 91.0]), start=(9, 5))
        res["timestamp"] = pre[0] + res["timestamp"]
        q = res["indicators"]["quote"][0]
        for key, extra in zip(("open", "high", "low", "close", "volume"), pre[1:]):
            q[key] = extra + q[key]
        r = at("2026-11-23 10:15", entry.parse_chart(json.dumps(doc), "AAA"))
        self.assertEqual((r["trigger"]["bar_index"], r["opening_range"]["high"]), (5, 101.3))

    def test_the_vwap_is_reported_on_every_trigger(self):
        r = at("2026-11-23 10:15")
        bs = I.bars(I.pad(HEAD, 72), first_open=99.8)[:6]
        self.assertEqual(r["trigger"]["vwap"], round(sum((b[1] + b[2] + b[3]) / 3 for b in bs) / 6, 2))
        self.assertTrue(r["trigger"]["above_vwap"])


class Gates(unittest.TestCase):
    def test_volume_gate(self):
        vol = [10000] * 5 + [30000] + [10000] * 66
        chart = chart_of(I.bars(I.pad(HEAD, 72), first_open=99.8, vol=vol))
        r = at("2026-11-23 10:15", chart, min_volume_x=1.5)
        self.assertEqual((r["status"], r["trigger"]["volume_x"]), ("triggered", 3.0))
        r = at("2026-11-23 10:15", min_volume_x=1.5)                # every bar is 1.0x
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["reason"], "the 10:10 bar closed above at Rs 102.30 but on 1.00x the range's volume, "
                                      "under your 1.50x")

    def spiky(self):
        bl = I.bars(I.pad(HEAD, 72), first_open=99.8)
        bl[3] = (100.5, 130.0, 100.2, 100.8, 500000)    # a huge wick drags the VWAP far above the range
        return chart_of(bl)

    def test_above_vwap_gate(self):
        r = at("2026-11-23 10:15", self.spiky())
        self.assertEqual((r["status"], r["trigger"]["above_vwap"]), ("triggered", False))
        r = at("2026-11-23 10:15", self.spiky(), above_vwap=True)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("closed above at Rs 102.30 but under the session VWAP Rs ", r["reason"])
        self.assertIn("TRIGGERED", "\n".join(entry.describe(at("2026-11-23 10:15", self.spiky()))))

    def test_no_entries_before_moves_the_trigger(self):
        cfg = dict(entry.DEFAULTS, no_entries_before="09:50")
        r = at("2026-11-23 10:15", cfg=cfg)
        self.assertEqual((r["trigger"]["bar_start"], r["trigger"]["price"]), ("09:45", 102.4))

    def test_level_setup(self):
        r = at("2026-11-23 10:15", setup="level", level=101.5)
        self.assertEqual((r["status"], r["trigger"]["bar_start"], r["level"]), ("triggered", "09:40", 101.5))
        with self.assertRaises(entry.EntryError):
            at("2026-11-23 10:15", setup="level")

    def test_a_longer_range(self):
        r = at("2026-11-23 10:15", or_minutes=30)
        self.assertEqual(r["opening_range"]["of"], 6)
        self.assertEqual(r["level"], 102.3)                   # 09:15-09:45 range high (the 09:40 bar's high)
        with self.assertRaises(entry.EntryError):
            at("2026-11-23 10:15", or_minutes=12)


class DataChecks(unittest.TestCase):
    def test_a_missing_range_bar_gives_no_signal(self):
        doc = I.v8("AAA.NS", {DAY: I.bars(I.pad(HEAD, 72), first_open=99.8)})
        res = doc["chart"]["result"][0]
        i = res["timestamp"].index(I.ts(DAY, 9, 20))
        del res["timestamp"][i]
        for v in res["indicators"]["quote"][0].values():
            del v[i]
        r = at("2026-11-23 10:15", entry.parse_chart(json.dumps(doc), "AAA"))
        self.assertEqual((r["status"], r["trigger"]), ("no-data", None))
        self.assertIn("the opening range has 2 of 3 bars", r["reason"])
        self.assertIn("would trigger early", r["reason"])

    def test_a_range_bar_yahoo_has_not_posted_yet_is_waiting_not_missing(self):
        chart = chart_of(I.bars([100.0, 101.0, 100.5], first_open=99.8))          # live: only 3 bars so far
        chart["data_time"] = I.ts(DAY, 9, 29) + 50                                   # last trade 09:29:50
        r = entry.evaluate(chart, entry.parse_now("2026-11-23 09:30:10"))
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertIn("the 9:25 range bar is not posted yet", r["reason"])
        r = entry.evaluate(chart, entry.parse_now("2026-11-23 09:31:30"))            # a minute later it counts
        self.assertEqual(r["opening_range"]["bars"], 3)

    def test_an_impossible_bar_gives_no_signal(self):
        bl = I.bars(I.pad(HEAD, 72), first_open=99.8)
        o, h, l, c, v = bl[5]
        bl[5] = (o, l, h, c, v)                                                       # high below low
        r = at("2026-11-23 10:15", chart_of(bl))
        self.assertEqual((r["status"], r["trigger"]), ("no-data", None))
        self.assertEqual(r["reason"], "bad data: high below low in the 9:40 bar. No signal from a chart that "
                                      "cannot be right")

    def test_no_bars_at_all(self):
        r = at("2026-11-23 10:15", chart_of([]))
        self.assertEqual(r["status"], "no-data")


class SessionHours(unittest.TestCase):
    def flat(self, n=75):
        return chart_of(I.bars([100.0] * n, first_open=100.0))

    def test_default_cutoff_follows_the_stock_group(self):
        self.assertEqual(at("2026-11-23 10:15")["no_entries_after"], "14:45")                    # any: 15:15 end
        self.assertEqual(at("2026-11-23 10:15", group="cas")["no_entries_after"], "14:45")
        self.assertEqual(at("2026-11-23 10:15", group="other")["no_entries_after"], "15:00")     # 15:30 end
        self.assertEqual(at("2026-11-23 10:15")["closes"], "15:15")
        self.assertEqual(at("2026-11-23 10:15", group="other")["closes"], "15:30")

    def test_from_7_dec_every_group_runs_to_1530(self):
        chart = chart_of(I.bars([100.0] * 75, first_open=100.0), day=D(2026, 12, 7))
        r = entry.evaluate(chart, entry.parse_now("2026-12-07 10:15"), group="cas")
        self.assertEqual((r["closes"], r["no_entries_after"]), ("15:30", "15:00"))

    def test_expiry_at_the_cutoff(self):
        r = at("2026-11-23 14:44", self.flat())
        self.assertEqual(r["status"], "waiting")
        r = at("2026-11-23 14:45", self.flat())
        self.assertEqual((r["status"], r["phase"]), ("expired", "after-cutoff"))
        self.assertEqual(r["reason"], "no close above the range high Rs 100.30 by 14:45, the last time for a new entry")
        r = at("2026-11-23 14:45", self.flat(), group="other")
        self.assertEqual(r["status"], "waiting")
        r = at("2026-11-23 15:00", self.flat(), group="other")
        self.assertEqual(r["status"], "expired")

    def test_a_breakout_after_the_cutoff_does_not_count(self):
        bl = I.bars([100.0] * 71 + [102.0], first_open=100.0)    # bar 71 starts 15:10, ends 15:15
        r = at("2026-11-23 15:20", chart_of(bl))
        self.assertEqual((r["status"], r["trigger"]), ("expired", None))
        self.assertEqual(r["phase"], "after-close")

    def test_the_closing_auction_bars_are_not_candles(self):
        r = at("2026-11-23 15:40", self.flat(75))
        self.assertEqual(r["bars_closed"], 72)
        self.assertEqual(r["_bars"][-1]["et"], datetime.datetime(2026, 11, 23, 15, 10))

    def test_holiday_and_weekend(self):
        r = at("2026-11-24 10:15")
        self.assertEqual((r["status"], r["phase"], r["next_open"]), ("no-data", "closed", "2026-11-25 09:15"))
        self.assertIn("the NSE is closed today (NSE holiday: Prakash Gurpurb", r["reason"])
        r = at("2026-11-28 10:15")
        self.assertEqual((r["reason"], r["next_open"]), ("the NSE is closed today (weekend)", "2026-11-30 09:15"))

    def test_a_year_the_holiday_list_does_not_cover(self):
        chart = chart_of(I.bars([100.0] * 75, first_open=100.0), day=D(2027, 1, 20))
        r = entry.evaluate(chart, entry.parse_now("2027-01-20 10:15"))
        self.assertFalse(r["calendar_known"])
        self.assertIn("Holiday list covers 2026 only: check nseindia.com", "\n".join(entry.describe(r)))

    def test_bad_group(self):
        with self.assertRaises(entry.EntryError):
            at("2026-11-23 10:15", group="nifty")


class Words(unittest.TestCase):
    def test_describe_a_trigger(self):
        text = "\n".join(entry.describe(at("2026-11-23 10:15")))
        for want in ("AAA  Mon 2026-11-23 10:15 IST  opening-range breakout (15-minute range) on 5-minute bars",
                     "Opening range 9:15-9:30: high Rs 101.30, low Rs 99.50 (3 of 3 bars).",
                     "TRIGGERED 9:45 AM at Rs 102.00: the 9:40 bar (bar 5) closed above the range high Rs 101.30 "
                     "(+0.69%, above VWAP Rs 100.79).",
                     "That was 30 minutes ago", "can lag. Check the live price in your own app", "Nothing was placed.",
                     "/trader-exit"):
            self.assertIn(want, text)

    def test_a_fresh_trigger_says_get_in_if_you_take_it(self):
        text = "\n".join(entry.describe(at("2026-11-23 09:46")))
        self.assertIn("This is the moment to get in, if you take the trade. Nothing was placed.", text)
        self.assertNotIn("That was", text)

    def test_describe_waiting_and_expired(self):
        text = "\n".join(entry.describe(at("2026-11-23 09:30")))
        self.assertIn("WAITING  last close Rs 100.50", text)
        text = "\n".join(entry.describe(at("2026-11-23 14:45", I_flat())))
        self.assertIn("EXPIRED", text)
        self.assertIn("No entry on this setup today. Nothing was placed.", text)

    def test_bar_line(self):
        r = at("2026-11-23 10:15")
        lines = {b["slot"]: entry.bar_line(b, r) for b in r["_bars"]}
        self.assertIn("opening range set: high Rs 101.30, low Rs 99.50", lines[2])
        self.assertEqual(lines[5], " 9:40 bar  closed Rs 102.00  TRIGGERED: closed above Rs 101.30 (+0.69%). "
                                   "Get in now if you take it: 9:45 AM, Rs 102.00")
        self.assertIn("Rs 0.10 under Rs 101.30", lines[4])


def I_flat():
    return chart_of(I.bars([100.0] * 75, first_open=100.0))


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_defaults_and_overrides(self):
        self.assertEqual(entry.read_config(None), {"no_entries_before": "09:30", "no_entries_after": None})
        p = os.path.join(self.tmp, "config.json")
        with open(p, "w") as fh:
            json.dump({"no_entries_before": "09:45", "no_entries_after": "14:30"}, fh)
        self.assertEqual(entry.read_config(p), {"no_entries_before": "09:45", "no_entries_after": "14:30"})
        r = at("2026-11-23 14:30", I_flat(), cfg=entry.read_config(p))
        self.assertEqual((r["status"], r["no_entries_after"]), ("expired", "14:30"))

    def test_a_cutoff_past_the_session_end_is_clamped(self):
        r = at("2026-11-23 10:15", cfg={"no_entries_before": "09:30", "no_entries_after": "15:25"})
        self.assertEqual(r["no_entries_after"], "15:15")

    def test_bad_time_is_an_error(self):
        p = os.path.join(self.tmp, "config.json")
        with open(p, "w") as fh:
            json.dump({"no_entries_before": "9.45"}, fh)
        with self.assertRaises(entry.EntryError) as cm:
            entry.read_config(p)
        self.assertIn("Indian time", str(cm.exception))


class Time(unittest.TestCase):
    def test_parse_now_is_indian_time(self):
        self.assertEqual(entry.parse_now("2026-11-23 09:15"), datetime.datetime(2026, 11, 23, 3, 45))
        self.assertEqual(entry.parse_now("2026-11-23 09:15 IST"), datetime.datetime(2026, 11, 23, 3, 45))
        self.assertEqual(entry.parse_now("2026-11-23T03:45:00Z"), datetime.datetime(2026, 11, 23, 3, 45))
        with self.assertRaises(entry.EntryError):
            entry.parse_now("soon")

    def test_symbols(self):
        self.assertEqual(entry.yahoo_symbol("m&m"), "M&M.NS")
        self.assertEqual(entry.chart_path("M&M", "1d", "5m"), "/v8/finance/chart/M%26M.NS?range=1d&interval=5m")

    def test_chart_range(self):
        t = D(2026, 11, 23)
        self.assertEqual(entry.chart_range(t, t, "5m"), "1d")
        self.assertEqual(entry.chart_range(D(2026, 11, 20), t, "5m"), "5d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(D(2026, 10, 1), t, "1m")


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None,
            "cfg": dict(entry.DEFAULTS), "group": "any", "above_vwap": False}

    def replay(self, chart, start="2026-11-23 09:00", opts=None, until=None):
        clock = entry.SimClock(entry.parse_now(start))
        lines, trig = [], []
        code, res = entry.watch_loop(lambda now: chart, clock.now, clock.sleep, lines.append, opts or self.OPTS,
                                     until=until, on_trigger=lambda r, n: trig.append(r))
        return code, res, lines, trig

    def test_replay_to_a_trigger(self):
        code, res, lines, trig = self.replay(chart_of())
        self.assertEqual(code, 0)
        self.assertEqual(len(trig), 1)
        self.assertEqual(res["trigger"]["price"], 102.0)
        text = "\n".join(lines)
        self.assertIn("09:00 IST  not open yet: watching from the first bar's close at 9:20.", text)
        self.assertIn("opening range set: high Rs 101.30, low Rs 99.50", text)
        self.assertIn("TRIGGERED: closed above Rs 101.30 (+0.69%). Get in now if you take it: 9:45 AM, Rs 102.00", text)

    def test_replay_to_expiry(self):
        code, res, lines, _ = self.replay(I_flat())
        self.assertEqual((code, res["status"]), (1, "expired"))
        self.assertIn("EXPIRED  no close above the range high Rs 100.30 by 14:45", "\n".join(lines))

    def test_replay_stops_at_until(self):
        until = datetime.datetime(2026, 11, 23, 11, 0)
        code, res, lines, _ = self.replay(I_flat(), until=until)
        self.assertEqual(code, 1)
        self.assertIn("Stopped at --until 11:00 without a trigger.", "\n".join(lines))

    def test_a_holiday_has_nothing_to_watch(self):
        code, res, lines, _ = self.replay(chart_of(), start="2026-11-24 09:00")
        self.assertEqual((code, res), (1, None))
        self.assertIn("market closed today (NSE holiday: Prakash Gurpurb", lines[0])


class Commands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._get = entry.yahoo_get
        entry.yahoo_get = lambda *a, **k: self.fail("the network was touched")
        self.addCleanup(setattr, entry, "yahoo_get", self._get)
        self.put("AAA.json", I.v8("AAA.NS", {DAY: I.bars(I.pad(HEAD, 72), first_open=99.8)}))

    def put(self, name, doc):
        with open(os.path.join(self.tmp, name), "w") as fh:
            json.dump(doc, fh)

    def p(self, name):
        return os.path.join(self.tmp, name)

    def test_check_writes_the_contract_file(self):
        out = self.p("entry.json")
        code, stdout, _ = run(["check", "AAA", "--now", "2026-11-23 10:15", "--offline", self.tmp, "--write", out])
        self.assertEqual(code, 0)
        doc = jload(out)
        for k, v in (("created_by", "trader-entry"), ("schema_version", 2), ("symbol", "AAA"), ("exchange", "NSE"),
                     ("currency", "INR"), ("timezone", "Asia/Kolkata"), ("group", "any"), ("setup", "orb"),
                     ("side", "buy"), ("status", "triggered"), ("time", "9:45 AM"), ("bar_start", "09:40"),
                     ("price", 102.0), ("level", 101.3), ("interval", "5m"), ("source", "yahoo"),
                     ("checked_at", "2026-11-23T04:45:00Z"), ("checked_at_ist", "2026-11-23 10:15:00")):
            self.assertEqual(doc[k], v, k)
        self.assertEqual(doc["opening_range"], {"high": 101.3, "low": 99.5, "minutes": 15})
        self.assertEqual(doc["vwap"], 100.79)
        self.assertIn("can lag", doc["price_note"])
        self.assertIn("TRIGGERED 9:45 AM at Rs 102.00", stdout)

    def test_check_accepts_the_yahoo_form_of_the_symbol(self):
        code, stdout, _ = run(["check", "aaa.ns", "--now", "2026-11-23 10:15", "--offline", self.tmp])
        self.assertEqual(code, 0)

    def test_check_waiting_exits_1(self):
        code, stdout, _ = run(["check", "AAA", "--now", "2026-11-23 09:30", "--offline", self.tmp])
        self.assertEqual(code, 1)
        self.assertIn("WAITING", stdout)

    def test_check_json(self):
        code, stdout, _ = run(["check", "AAA", "--now", "2026-11-23 10:15", "--offline", self.tmp, "--json"])
        doc = json.loads(stdout)
        self.assertEqual((doc["status"], doc["trigger"]["price"], doc["group"]), ("triggered", 102.0, "any"))
        self.assertNotIn("_bars", doc)

    def test_group_flag_changes_the_hours(self):
        code, stdout, _ = run(["check", "AAA", "--now", "2026-11-23 10:15", "--offline", self.tmp, "--json",
                               "--group", "other"])
        self.assertEqual(json.loads(stdout)["closes"], "15:30")

    def test_a_chart_for_another_symbol_is_refused(self):
        self.put("BBB.json", I.v8("AAA.NS", {DAY: I.bars(I.pad(HEAD, 72), first_open=99.8)}))
        code, _, err = run(["check", "BBB", "--now", "2026-11-23 10:15", "--offline", self.tmp])
        self.assertEqual(code, 1)
        self.assertIn("the chart file holds AAA.NS, not BBB", err)

    def test_wrong_bar_size_is_refused(self):
        code, _, err = run(["check", "AAA", "--interval", "1m", "--now", "2026-11-23 10:15", "--offline", self.tmp])
        self.assertEqual(code, 1)
        self.assertIn("AAA has 5m bars, not 1m: use --interval 5m", err)

    def test_an_ampersand_symbol(self):
        self.put("M&M.json", I.v8("M&M.NS", {DAY: I.bars(I.pad(HEAD, 72), first_open=99.8)}))
        code, stdout, _ = run(["check", "M&M", "--now", "2026-11-23 10:15", "--offline", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("M&M  Mon 2026-11-23 10:15 IST", stdout)

    def test_shorts_and_a_stray_level_are_refused(self):
        code, _, err = run(["check", "AAA", "--side", "short", "--now", "2026-11-23 10:15", "--offline", self.tmp])
        self.assertEqual(code, 1)
        self.assertIn("Longs only", err)
        code, _, err = run(["check", "AAA", "--level", "100", "--now", "2026-11-23 10:15", "--offline", self.tmp])
        self.assertEqual(code, 1)

    def test_watch_offline_replays_and_writes(self):
        out = self.p("entry.json")
        code, stdout, _ = run(["watch", "AAA", "--now", "2026-11-23 09:00", "--offline", self.tmp, "--out", out])
        self.assertEqual(code, 0)
        self.assertEqual(jload(out)["price"], 102.0)
        self.assertIn("Wrote", stdout)

    def test_watchlist_table_and_groups(self):
        self.put("BBB.json", I.v8("BBB.NS", {DAY: I.bars([100.0] * 75, first_open=100.0)}))
        self.put("watchlist.json", {"hits": [{"symbol": "AAA", "group": "cas"}, {"symbol": "BBB", "group": "other"}]})
        code, stdout, _ = run(["watchlist", "--watchlist", self.p("watchlist.json"), "--now", "2026-11-23 10:15",
                               "--offline", self.tmp])
        self.assertEqual(code, 0)
        self.assertIn("AAA           triggered  9:45 AM   Rs 102.00", stdout)
        self.assertIn("1 of 2 triggered.", stdout)
        code, stdout, _ = run(["watchlist", "--watchlist", self.p("watchlist.json"), "--now", "2026-11-23 10:15",
                               "--offline", self.tmp, "--json"])
        rows = {r["symbol"]: r["result"] for r in json.loads(stdout)}
        self.assertEqual((rows["AAA"]["closes"], rows["BBB"]["closes"]), ("15:15", "15:30"))
        self.assertEqual(rows["BBB"]["group"], "other")

    def test_watchlist_without_a_file(self):
        code, _, err = run(["watchlist", "--watchlist", self.p("nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("run /trader-scanner first", err)

    def test_missing_offline_file(self):
        code, _, err = run(["check", "ZZZ", "--now", "2026-11-23 10:15", "--offline", self.tmp])
        self.assertEqual(code, 1)
        self.assertIn("no offline file for ZZZ", err)


if __name__ == "__main__":
    unittest.main()
