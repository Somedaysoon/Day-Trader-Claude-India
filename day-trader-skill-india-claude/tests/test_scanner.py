"""trader-scanner on NSE data. Every chart here is SYNTHETIC (tests/_india.py), not a real capture."""

import copy
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

scan = load("trader-scanner", "scan")
D = datetime.date
DT = datetime.datetime

DAY, FRI, THU = D(2026, 11, 23), D(2026, 11, 20), D(2026, 11, 19)
HEAD = [100.0, 101.0, 100.5, 100.8, 101.2, 102.0, 102.4, 102.1, 102.3]
# range bars 09:15-09:25: high 101.3, low 99.5. First close above 101.3 is the 09:40 bar (102.0).


def aaa_sessions(head=HEAD, first_open=99.8, vol=10000, prior=True, n=72):
    s = {DAY: I.bars(I.pad(head, n), first_open=first_open, vol=vol)}
    if prior:
        for d in (THU, FRI):
            s[d] = I.bars(I.pad([98.0], 72), vol=8000)
    return s


def index_sessions():
    return {DAY: I.bars(I.pad([24020, 24030, 24025, 24030, 24035, 24040], 72), first_open=24000, spread=5, vol=0)}


def series(sym="AAA", **kw):
    return scan.parse_chart(I.v8(sym + ".NS", aaa_sessions(**kw)), sym)


def now(text):
    return scan.parse_now(text)


def jload(path):
    with open(path) as fh:
        return json.load(fh)


def read(path):
    with open(path) as fh:
        return fh.read()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._http = scan.http_get
        scan.http_get = lambda *a, **k: self.fail("the network was touched")
        self.addCleanup(setattr, scan, "http_get", self._http)

    def put(self, name, doc):
        with open(os.path.join(self.tmp, name), "w") as fh:
            json.dump(doc, fh)

    def p(self, name):
        return os.path.join(self.tmp, name)


class OrbHit(unittest.TestCase):
    def go(self, stage1, cutoff=DT(2026, 11, 23, 10, 15), **kw):
        return scan.scan_orb(stage1, ["AAA"], DAY, cutoff, 15, **kw)

    def test_first_bar_to_close_above_the_range_high(self):
        hits, counts, failed = self.go({"AAA": series()})
        h = hits[0]
        self.assertEqual((h["symbol"], h["price"], h["at"], h["bar_start"], h["bar_index"]),
                         ("AAA", 102.0, "9:45 AM", "09:40", 5))
        self.assertEqual((h["or_high"], h["or_low"], h["pct_over"]), (101.3, 99.5, 0.69))
        self.assertEqual(h["setups"], ["orb"])
        self.assertEqual((counts["scanned"], counts["candidates"], counts["confirmed"], counts["hits"]), (1, 1, 1, 1))
        self.assertEqual(failed, {})

    def test_the_range_is_0915_to_0930_not_0930_to_0945(self):
        bars = scan.session_bars(series(), DAY, DT(2026, 11, 23, 10, 15))
        self.assertEqual(len(bars), 12)
        self.assertEqual(bars[0]["start"], DT(2026, 11, 23, 9, 15))
        self.assertEqual(bars[-1]["start"], DT(2026, 11, 23, 10, 10))

    def test_a_forming_bar_is_not_used(self):
        hits, _, _ = self.go({"AAA": series()}, cutoff=DT(2026, 11, 23, 9, 44))   # 09:40 bar closes 09:45
        self.assertEqual(hits, [])
        hits, _, _ = self.go({"AAA": series()}, cutoff=DT(2026, 11, 23, 9, 45))
        self.assertEqual(hits[0]["bar_start"], "09:40")

    def test_signals_vwap_volume_previous_high_and_nifty(self):
        idx = scan.parse_chart(I.v8("^NSEI", index_sessions()), "^NSEI")
        hits, counts, _ = self.go({"AAA": series()}, index_series=idx)
        sig = hits[0]["signals"]
        bs = I.bars(I.pad(HEAD, 72), first_open=99.8)[:6]
        self.assertEqual(sig["vwap"], round(sum((b[1] + b[2] + b[3]) / 3 for b in bs) / 6, 2))
        self.assertTrue(sig["above_vwap"])
        self.assertEqual(sig["rel_volume"], 1.25)            # 6 bars x 10,000 against 6 x 8,000
        self.assertEqual((sig["prev_day_high"], sig["above_prev_day_high"]), (98.3, True))
        self.assertEqual(sig["vs_nifty_pp"], round((102.0 / 99.8 - 1) * 100 - (24040 / 24000 - 1) * 100, 2))
        self.assertTrue(counts["index_available"])
        self.assertEqual(sig["turnover_cr"], round(sum(c * 10000 for c in I.pad(HEAD, 72)[:12]) / 1e7, 2))

    def test_signals_are_left_empty_when_the_data_is_not_there(self):
        hits, counts, _ = self.go({"AAA": series(prior=False)})
        sig = hits[0]["signals"]
        self.assertIsNone(sig["rel_volume"])
        self.assertIsNone(sig["prev_day_high"])
        self.assertIsNone(sig["above_prev_day_high"])
        self.assertIsNone(sig["vs_nifty_pp"])
        self.assertFalse(counts["index_available"])
        self.assertIsNotNone(sig["vwap"])

    def test_one_prior_session_is_not_enough_for_relative_volume(self):
        s = aaa_sessions()
        del s[THU]
        hits, _, _ = self.go({"AAA": scan.parse_chart(I.v8("AAA.NS", s), "AAA")})
        self.assertIsNone(hits[0]["signals"]["rel_volume"])
        self.assertEqual(hits[0]["signals"]["prev_day_high"], 98.3)

    def test_signals_never_filter(self):
        weak = aaa_sessions()
        weak[FRI] = I.bars(I.pad([200.0], 72), vol=8000)        # yesterday's high is far above the breakout
        hits, _, _ = self.go({"AAA": scan.parse_chart(I.v8("AAA.NS", weak), "AAA")})
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0]["signals"]["above_prev_day_high"])

    def test_a_flat_chart_is_a_candidate_but_not_a_hit(self):
        hits, counts, _ = self.go({"AAA": series(head=[100.0] * 12, first_open=100.0)})
        self.assertEqual((hits, counts["candidates"], counts["confirmed"]), ([], 1, 1))

    def test_a_missing_bar_makes_the_chart_unusable(self):
        s = series()
        i = s["t"].index(I.ts(DAY, 9, 35))
        for k in ("t", "c", "o", "h", "l", "v"):
            del s[k][i]
        hits, counts, _ = self.go({"AAA": s})
        self.assertEqual((hits, counts["unusable"], counts["scanned"]), ([], 1, 0))

    def test_an_impossible_bar_is_dropped_as_bad_data(self):
        s = series()
        i = s["t"].index(I.ts(DAY, 9, 40))
        s["h"][i], s["l"][i] = s["l"][i], s["h"][i]              # high below low
        hits, counts, failed = self.go({"AAA": s})
        self.assertEqual(hits, [])
        self.assertEqual(counts["bad_data"], 1)
        self.assertIn("bad data: high below low at 09:40", failed["AAA"])

    def test_out_of_order_candles_are_dropped_as_bad_data(self):
        s = series()
        s["t"][20], s["t"][21] = s["t"][21], s["t"][20]
        hits, counts, failed = self.go({"AAA": s}, confirm=lambda sym: s)
        self.assertEqual(counts["bad_data"], 1)
        self.assertIn("out of order", failed["AAA"])

    def test_price_and_turnover_floors(self):
        _, counts, _ = self.go({"AAA": series()}, min_price=200)
        self.assertEqual((counts["illiquid"], counts["scanned"]), (1, 0))
        _, counts, _ = self.go({"AAA": series()}, min_turnover=5e8)
        self.assertEqual((counts["illiquid"], counts["scanned"]), (1, 0))
        _, counts, _ = self.go({"AAA": series()}, min_price=20, min_turnover=5e6)
        self.assertEqual(counts["scanned"], 1)

    def test_two_stages_a_spark_candidate_is_confirmed_with_real_highs(self):
        spark = scan.parse_spark(I.spark({"AAA.NS": aaa_sessions()}))["AAA"]
        self.assertIsNone(spark["h"])
        full = series()
        hits, counts, failed = self.go({"AAA": spark}, confirm=lambda s: full)
        self.assertEqual((len(hits), hits[0]["price"], counts["confirmed"]), (1, 102.0, 1))

    def test_a_confirm_failure_is_reported_not_swallowed(self):
        spark = scan.parse_spark(I.spark({"AAA.NS": aaa_sessions()}))["AAA"]

        def boom(sym):
            raise scan.ScanError("Yahoo answered HTTP 429")
        hits, counts, failed = self.go({"AAA": spark}, confirm=boom)
        self.assertEqual((hits, failed), ([], {"AAA": "Yahoo answered HTTP 429"}))

    def test_max_confirm_leaves_the_rest_unconfirmed(self):
        stage1 = {s: series(s) for s in ("AAA", "BBB", "CCC")}
        hits, counts, _ = scan.scan_orb(stage1, ["AAA", "BBB", "CCC"], DAY, DT(2026, 11, 23, 10, 15), 15,
                                        confirm=lambda s: stage1[s], max_confirm=1)
        self.assertEqual((counts["candidates"], counts["unconfirmed"], counts["confirmed"]), (3, 2, 1))

    def test_group_is_carried_to_the_hit(self):
        hits, _, _ = self.go({"AAA": series()}, groups={"AAA": "cas"})
        self.assertEqual(hits[0]["group"], "cas")
        hits, _, _ = self.go({"AAA": series()})
        self.assertEqual(hits[0]["group"], "any")


class SessionRules(unittest.TestCase):
    def test_bars_from_the_closing_auction_are_not_used(self):
        s = series(n=75)                                         # a chart that runs on to 15:25
        bars = scan.session_bars(s, DAY, DT(2026, 11, 23, 15, 30))
        self.assertEqual((len(bars), bars[-1]["start"]), (72, DT(2026, 11, 23, 15, 10)))

    def test_from_7_dec_the_session_has_75_bars(self):
        day = D(2026, 12, 7)
        s = scan.parse_chart(I.v8("AAA.NS", {day: I.bars(I.pad(HEAD, 75), first_open=99.8)}), "AAA")
        bars = scan.session_bars(s, day, DT(2026, 12, 7, 15, 30))
        self.assertEqual((len(bars), bars[-1]["start"]), (75, DT(2026, 12, 7, 15, 25)))

    def test_pre_open_prints_before_0915_are_not_candles(self):
        sessions = {DAY: I.bars(I.pad(HEAD, 72), first_open=99.8)}
        t, o, h, l, c, v = I.day_series(DAY, I.bars([90.0, 91.0]), start=(9, 5))   # 09:05 and 09:10
        s = series()
        s["t"] = t + s["t"]
        for key, extra in (("o", o), ("h", h), ("l", l), ("c", c), ("v", v)):
            s[key] = extra + s[key]
        bars = scan.session_bars(s, DAY, DT(2026, 11, 23, 10, 15))
        self.assertEqual(bars[0]["start"], DT(2026, 11, 23, 9, 15))
        self.assertEqual(len(bars), 12)


class Window(unittest.TestCase):
    def w(self, text, at=None, until=None, day=None):
        return scan.orb_window(now(text), at, 15, until, day)

    def test_holiday_is_refused_with_the_name(self):
        with self.assertRaises(scan.ScanError) as cm:
            self.w("2026-11-24 10:15")
        self.assertIn("NSE holiday: Prakash Gurpurb", str(cm.exception))

    def test_too_early_names_the_ist_range(self):
        with self.assertRaises(scan.ScanError) as cm:
            self.w("2026-11-23 09:34")
        self.assertIn("9:15 AM to 9:30 AM", str(cm.exception))
        self.assertIn("IST", str(cm.exception))

    def test_first_close_after_the_range_is_0935(self):
        self.assertEqual(self.w("2026-11-23 09:35")[1], DT(2026, 11, 23, 9, 35))

    def test_default_last_entry_follows_the_session_end(self):
        self.assertEqual(self.w("2026-11-23 14:45")[1], DT(2026, 11, 23, 14, 45))
        with self.assertRaises(scan.ScanError) as cm:
            self.w("2026-11-23 14:46")
        self.assertIn("expired", str(cm.exception))
        # from 7 Dec the session runs to 15:30, so entries stay open until 15:00
        self.assertEqual(self.w("2026-12-07 15:00")[1], DT(2026, 12, 7, 15, 0))
        with self.assertRaises(scan.ScanError):
            self.w("2026-12-07 15:01")

    def test_an_explicit_until_wins(self):
        self.assertEqual(self.w("2026-11-23 15:05", until=datetime.time(15, 10))[1], DT(2026, 11, 23, 15, 5))

    def test_after_continuous_trading_ends(self):
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(now("2026-11-23 15:30"), datetime.time(15, 20), 15, datetime.time(15, 29), DAY)
        self.assertIn("after continuous trading ends at 3:15 PM", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            self.w("2026-11-23 15:16", until=datetime.time(15, 29))
        self.assertIn("ended at 3:15 PM IST", str(cm.exception))

    def test_or_minutes_must_sit_on_the_grid(self):
        with self.assertRaises(scan.ScanError):
            scan.orb_window(now("2026-11-23 10:15"), None, 12, None)


class Symbols(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertEqual(scan.to_yahoo("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.to_yahoo("M&M"), "M%26M.NS")
        self.assertEqual(scan.to_yahoo("^NSEI"), "%5ENSEI")
        self.assertEqual(scan.spark_path(["TCS", "INFY"], "1d", "5m"),
                         "/v7/finance/spark?symbols=TCS.NS,INFY.NS&range=1d&interval=5m")
        self.assertEqual(scan.chart_path("^NSEI", "5d", "5m"), "/v8/finance/chart/%5ENSEI?range=5d&interval=5m")

    def test_spark_symbols_come_back_without_the_suffix(self):
        got = scan.parse_spark(I.spark({"RELIANCE.NS": aaa_sessions(), "M&M.NS": aaa_sessions()}))
        self.assertEqual(sorted(got), ["M&M", "RELIANCE"])


class Daily(unittest.TestCase):
    CLOSES = [100 + (i % 5) * 0.2 for i in range(39)] + [105.0]

    def s(self, last_day=DAY, closes=None, **kw):
        return scan.parse_chart(I.daily("AAA.NS", closes or self.CLOSES, last_day, **kw), "AAA")

    def test_breakout_once_the_day_is_settled(self):
        hits, counts, last = scan.scan_daily({"AAA": self.s(volume=600000)}, ["AAA"], now("2026-11-23 15:36"),
                                             20.0, 5e7)
        self.assertEqual([h["symbol"] for h in hits], ["AAA"])
        self.assertEqual(hits[0]["setups"], ["breakout_20d"])
        self.assertEqual((hits[0]["turnover_cr"], last), (6.3, DAY))

    def test_todays_bar_is_dropped_until_the_auction_is_done(self):
        for t in ("2026-11-23 15:20", "2026-11-23 15:34"):
            hits, _, last = scan.scan_daily({"AAA": self.s(volume=600000)}, ["AAA"], now(t), 20.0, 5e7)
            self.assertEqual((hits, last), ([], FRI))

    def test_the_wait_is_longer_from_7_dec(self):
        s = self.s(last_day=D(2026, 12, 7), volume=600000)
        self.assertEqual(scan.scan_daily({"AAA": s}, ["AAA"], now("2026-12-07 15:36"), 20.0, 5e7)[0], [])
        self.assertEqual(len(scan.scan_daily({"AAA": s}, ["AAA"], now("2026-12-07 15:41"), 20.0, 5e7)[0]), 1)

    def test_liquidity_is_traded_value_in_rupees(self):
        _, counts, _ = scan.scan_daily({"AAA": self.s(volume=100000)}, ["AAA"], now("2026-11-23 15:36"), 20.0, 5e7)
        self.assertEqual((counts["illiquid"], counts["scanned"]), (1, 0))
        _, counts, _ = scan.scan_daily({"AAA": self.s(price=15.0, volume=9000000)}, ["AAA"],
                                       now("2026-11-23 15:36"), 20.0, 5e7)
        self.assertEqual(counts["illiquid"], 1)

    def test_a_short_history_is_counted(self):
        _, counts, _ = scan.scan_daily({"AAA": self.s(closes=self.CLOSES[-20:], volume=600000)}, ["AAA"],
                                       now("2026-11-23 15:36"), 20.0, 5e7)
        self.assertEqual(counts["short"], 1)


class Universe(Fixture):
    EQUITY = (
        "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
        "AAA,Alpha Industries Limited,EQ,01-JAN-1995,10,1,INE000A01011,10\n"
        "BBB,Beta Motors Ltd,EQ,02-FEB-2001,2,1,INE000B01012,2\n"
        "CCC,Gamma Trade-for-Trade Ltd,BE,04-APR-2010,10,1,INE000D01014,10\n"
        "DDD,Delta SME Ltd,SM,05-MAY-2022,10,1000,INE000E01015,10\n")
    FNO = "UNDERLYING,SYMBOL,JAN-26\nAlpha Industries Limited,AAA,500\n"
    INDEX = "Company Name,Industry,Symbol,Series,ISIN Code\nAlpha,Metals,AAA,EQ,INE000A01011\n"

    def files(self, **extra):
        for name, text in dict(eq=self.EQUITY, fno=self.FNO, idx=self.INDEX).items():
            with open(self.p(name + ".csv"), "w") as fh:
                fh.write(text)

    def test_eq_series_only_with_groups(self):
        self.files()
        out = self.p("universe.txt")
        code, stdout, _ = run(["universe", "--equity-file", self.p("eq.csv"), "--fno-file", self.p("fno.csv"),
                               "--out", out])
        self.assertEqual(code, 0)
        lines = [ln for ln in read(out).splitlines() if not ln.startswith("#")]
        self.assertEqual(lines, ["AAA|Alpha Industries Limited|cas", "BBB|Beta Motors Ltd|other"])
        self.assertIn("Kept 2 EQ-series stocks", stdout)
        self.assertIn("1 BE, 1 SM", stdout)
        self.assertIn("1 cas, 1 other", stdout)

    def test_an_index_list_narrows_it(self):
        self.files()
        out = self.p("universe.txt")
        run(["universe", "--equity-file", self.p("eq.csv"), "--index-file", self.p("idx.csv"), "--out", out])
        lines = [ln for ln in read(out).splitlines() if not ln.startswith("#")]
        self.assertEqual(lines, ["AAA|Alpha Industries Limited|any"])

    def test_without_an_fno_file_the_group_is_unknown_and_the_run_says_so(self):
        self.files()
        code, stdout, _ = run(["universe", "--equity-file", self.p("eq.csv"), "--out", self.p("u.txt")])
        self.assertIn("closing-auction group unknown", stdout)

    def test_a_blocked_download_gives_the_manual_route(self):
        def blocked(url, timeout=20):
            raise scan.urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        scan.http_get = blocked
        code, _, err = run(["universe", "--out", self.p("u.txt")])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 403", err)
        self.assertIn("--equity-file", err)

    def test_not_an_nse_file_is_a_plain_error(self):
        with open(self.p("junk.csv"), "w") as fh:
            fh.write("<html>Access Denied</html>")
        code, _, err = run(["universe", "--equity-file", self.p("junk.csv"), "--out", self.p("u.txt")])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE equity list", err)

    def test_universe_file_feeds_groups_and_symbols(self):
        self.files()
        out = self.p("universe.txt")
        run(["universe", "--equity-file", self.p("eq.csv"), "--fno-file", self.p("fno.csv"), "--out", out])
        self.assertEqual(scan.groups_from_file(out), {"AAA": "cas", "BBB": "other"})
        self.assertEqual(scan.symbols_from_file(out), ["AAA", "BBB"])


class EndToEnd(Fixture):
    def setUp(self):
        super().setUp()
        self.put("AAA.json", I.v8("AAA.NS", aaa_sessions()))
        self.put("NSEI.json", I.v8("^NSEI", index_sessions()))

    def orb(self, *extra):
        out = self.p("watchlist.json")
        code, stdout, err = run(["orb", "--offline", self.tmp, "--now", "2026-11-23 10:15", "--out", out,
                                 "--quiet"] + list(extra))
        return code, stdout, err, out

    def test_orb_writes_a_watchlist_with_the_coverage_fields(self):
        code, stdout, _, out = self.orb()
        self.assertEqual(code, 0)
        doc = jload(out)
        for k, v in (("schema_version", 2), ("exchange", "NSE"), ("currency", "INR"), ("timezone", "Asia/Kolkata"),
                     ("mode", "orb"), ("as_of", "2026-11-23 10:15 IST"), ("universe", 1), ("scanned", 1),
                     ("succeeded", 1), ("failed", 0), ("partial", False), ("source", "yahoo"),
                     ("index_available", True)):
            self.assertEqual(doc[k], v, k)
        self.assertEqual(doc["hits"][0]["symbol"], "AAA")
        self.assertEqual(doc["filters"], {"min_price": 20.0, "min_turnover_cr": 0.5})
        self.assertIn("Opening-range breakout scan, Mon 2026-11-23 10:15 AM IST", stdout)
        self.assertIn("range (9:15 AM to 9:30 AM)", stdout)
        self.assertIn("Rs 102.00", stdout)
        self.assertIn("above VWAP", stdout)

    def test_the_index_chart_is_never_scanned_as_a_stock(self):
        _, _, _, out = self.orb()
        self.assertEqual(jload(out)["universe"], 1)

    def test_a_chart_with_no_data_makes_the_scan_partial(self):
        self.put("ZZZ.json", {"chart": {"result": None, "error": {"description": "No data found"}}})
        code, stdout, _, out = self.orb()
        doc = jload(out)
        self.assertEqual((doc["failed"], doc["partial"], doc["universe"]), (1, True, 2))
        self.assertIn("PARTIAL: 1 symbols had no usable data", stdout)

    def test_offline_has_no_confirm_stage_so_max_confirm_cuts_nothing(self):
        # the unconfirmed-means-partial rule is covered by test_max_confirm_leaves_the_rest_unconfirmed
        for s in ("BBB", "CCC"):
            self.put(s + ".json", I.v8(s + ".NS", aaa_sessions()))
        code, _, _, out = self.orb("--max-confirm", "1")
        doc = jload(out)
        self.assertEqual((doc["unconfirmed"], doc["partial"], len(doc["hits"])), (0, False, 3))

    def test_the_universe_file_sets_the_group_on_the_hit(self):
        uni = self.p("universe.txt")
        with open(uni, "w") as fh:
            fh.write("AAA|Alpha Industries|cas\n")
        _, _, _, out = self.orb("--universe", uni)
        self.assertEqual(jload(out)["hits"][0]["group"], "cas")

    def test_no_hit_exits_1(self):
        self.put("AAA.json", I.v8("AAA.NS", aaa_sessions(head=[100.0] * 12, first_open=100.0)))
        code, stdout, _, _ = self.orb()
        self.assertEqual(code, 1)
        self.assertIn("No chart closed above its opening-range high by 10:15 AM", stdout)

    def test_a_holiday_is_a_clean_failure(self):
        code, _, err = run(["orb", "--offline", self.tmp, "--now", "2026-11-24 10:15", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday", err)

    def test_json_output_has_counts(self):
        code, stdout, _, _ = self.orb("--json")
        doc = json.loads(stdout)
        self.assertEqual(doc["counts"]["hits"], 1)
        self.assertEqual(doc["hits"][0]["signals"]["rel_volume"], 1.25)

    def test_daily_offline(self):
        self.put("AAA.json", I.daily("AAA.NS", Daily.CLOSES, DAY, volume=600000))
        os.remove(self.p("NSEI.json"))
        out = self.p("daily.json")
        code, stdout, _ = run(["daily", "--offline", self.tmp, "--now", "2026-11-23 15:36", "--out", out, "--quiet"])
        self.assertEqual(code, 0)
        doc = jload(out)
        self.assertEqual((doc["mode"], doc["as_of"], doc["hits"][0]["setups"]), ("daily", "2026-11-23 close",
                                                                              ["breakout_20d"]))
        self.assertIn("1 passed the liquidity filter (Rs 20.00+ and 5 crore+ traded)", stdout)


if __name__ == "__main__":
    unittest.main()
