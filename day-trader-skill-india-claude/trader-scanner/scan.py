#!/usr/bin/env python3
"""Chart Scanner for trader-scanner: scan NSE charts for setups. Standard library only.

    python3 scan.py universe --equity-file EQUITY_L.csv   # NSE list -> trader/universe.txt
    python3 scan.py daily                                 # before 9:15 AM IST: daily setups -> trader/watchlist.json
    python3 scan.py daily --symbols RELIANCE,TCS,INFY     # or --watchlist FILE, or --limit 200
    python3 scan.py orb                                   # after 9:35 AM IST: opening-range breakouts so far
    python3 scan.py orb --at 10:15 --or-minutes 15        # cut the clock at 10:15 AM IST
    python3 scan.py orb --offline folder --now "2026-11-23 10:15" --min-price 0

universe  Reads NSE's equity list (EQUITY_L.csv) and keeps the EQ series only: the
          trade-for-trade series (BE, BZ), SME and the rest cannot be traded
          intraday and are dropped, and the counts per series are printed. Give
          --index-file (any NSE index constituent list, for example the Nifty 500
          list) to keep only that index, and --fno-file (NSE's F&O lot-size file)
          to mark the stocks that take part in the closing auction session
          (reported as the F&O-eligible ones, group "cas"; the rest are "other").
          Without --equity-file the lists are downloaded from the NSE addresses
          in NSE_EQUITY_URL and NSE_FNO_URL. Those addresses and NSE's rules for
          automated downloads were not checked when this was written: if a
          download fails, fetch the file from NSE's website by hand and pass it
          with --equity-file. Output: one SYMBOL|Name|group line a stock.

daily     Pre-market scan of daily CLOSES (Yahoo spark: 20 symbols a request,
          3 months of 1-day bars, closes only; symbols are SYMBOL.NS). Liquidity
          from the spark meta: price >= --min-price (20 rupees) and traded value
          (price x last volume) >= --min-turnover-cr (5 crore rupees). Setups, from
          closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          Today's daily bar is dropped until the day is settled (the closing
          auction included, so 15:35 or 15:40 IST, not 15:30).

orb       Opening-range breakouts, 5-minute bars. The range is the first
          --or-minutes (15) of the session, 9:15 to 9:30 AM IST. Stage 1: spark
          closes for every symbol (1 day of 5-minute bars, 20 a request); a chart
          goes on only if a completed bar after the range closed at or above the
          highest close of the range (a necessary condition). Stage 2: each
          candidate's v8 chart (real highs and lows, 5 days so the earlier
          sessions can be compared): range high = the highest high of the range
          bars, a hit = the first completed bar to CLOSE above it, at that bar's
          close time. Stage 2 fetches at most --max-confirm (200) charts, the most
          traded candidates first (traded value so far), and says so when it
          stops short. A chart is usable only when every 5-minute bar from 9:15
          to the cutoff is there (a missing bar is a halt or no trades) and no bar
          has an impossible high, low or close. Floors: --min-price 20 (last
          close), --min-turnover-cr 0.5 (traded value so far).

          Each hit also carries SIGNALS, shown beside it and never used to filter:
          vwap (session VWAP through the trigger bar) and above_vwap; rel_volume
          (volume so far over the average of the earlier sessions at the same
          time, when two or more earlier sessions are in the chart);
          prev_day_high and above_prev_day_high; vs_nifty_pp (the stock's move
          since the open minus the Nifty 50's, in percentage points, when the
          index chart is available).

Hours: continuous trading is not always 9:15 to 3:30. Since 3 Aug 2026 the stocks
in the closing auction session stop at 3:15 PM, and from 7 Dec 2026 everything
runs to 3:30 PM again. The hours come from _common/data/sessions.json by date.
The scanner uses the earlier end when a stock's group is not known.

Bars: null closes are dropped, a bar off the 5-minute grid (the live tick,
like 10:49:45) is dropped, and a bar counts only once it is complete
(start + 5 minutes <= the clock). Times are Indian time (IST, UTC+05:30, no
daylight saving). --now pins the clock ("2026-11-23 10:15" IST; ...Z for UTC).

Yahoo's endpoints are free, unofficial and can lag. query2 first, query1 on
a 429 or 5xx, one retry after a pause on a 429. A polite pause between
requests (--pause). Partial results are reported as partial. Yahoo's NSE (.NS)
intraday coverage was not checked from the build environment.

--offline PATH reads files instead of the network: daily and orb take a folder
of <SYMBOL>.json v8 chart files or spark responses, or one file that is a spark
response, a v8 chart, or a bundle {"RELIANCE": <v8 json>, ...}. .json.gz is
fine. A chart named NSEI (or ^NSEI) is the Nifty 50 index, used for vs_nifty_pp.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import in_instruments as ins  # noqa: E402
from _common import in_market as mk  # noqa: E402
from _common import in_quality as quality  # noqa: E402

UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0; +https://github.com/Jakeschincariol/day-trader-skill)"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
# NSE's published lists. UNVERIFIED from the build environment (no network): confirm on a first run.
NSE_EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_FNO_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
SPARK_MAX = 20      # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3         # seconds between requests: be polite to free endpoints
BACKOFF = 5.0       # seconds to wait before the one retry after a 429
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MAX_CONFIRM = 200   # stage-2 v8 charts per orb scan, most traded first
MIN_DAILY_BARS = 30
DEFAULT_UNIVERSE = "trader/universe.txt"
INDEX_SYMBOL = "^NSEI"
# Placeholder floors in rupees; change them with --min-price and --min-turnover-cr.
DAILY_MIN_PRICE, DAILY_MIN_TURNOVER_CR = 20.0, 5.0
ORB_MIN_PRICE, ORB_MIN_TURNOVER_CR = 20.0, 0.5
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- time (Indian time, from _common)

to_ist = mk.to_ist
ist_to_utc = mk.ist_to_utc
ts_to_ist = mk.ts_to_ist
trading_day = mk.trading_day
clock_label = mk.clock_label
day_label = mk.day_label


def parse_now(text):
    return mk.parse_now(text, ScanError)


def parse_hhmm(text, flag="--at"):
    return mk.parse_hhmm(text, flag, ScanError)


def session(d, group="any"):
    """(open_time, close_time) of continuous trading in IST, or None if closed all day."""
    return mk.session(d, group)


# ---------------------------------------------------------------- network

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(path):
    """GET a Yahoo Finance path. query2 first, query1 on a 429 or 5xx, and one
    more round after BACKOFF seconds if a 429 was seen. 4xx other than 429 is final."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body  # Yahoo's JSON error ("No data found"), parsed by the caller
                raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


def to_yahoo(sym):
    """NSE symbol -> Yahoo symbol, URL-safe: RELIANCE -> RELIANCE.NS, ^NSEI -> %5ENSEI."""
    s = sym.strip().upper()
    if s.startswith("^"):
        return quote(s, safe="")
    return ins.yahoo_symbol(s)


def spark_path(symbols, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        ",".join(to_yahoo(s) for s in symbols), rng, interval)


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (to_yahoo(sym), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    """A Yahoo chart result (v8 or one spark response) -> plain lists."""
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc, wanted=None):
    """Spark JSON -> {SYM: series}. Symbols Yahoo does not know are simply missing
    from the answer. Yahoo's RELIANCE.NS comes back as RELIANCE."""
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise ScanError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or ""
        sym = (wanted or {}).get(ysym.upper(), ins.from_yahoo(ysym))
        out[sym] = series_from_result(resp[0])
    if err and not out:
        raise ScanError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, sym=""):
    """v8 chart JSON -> series. Raises ScanError for Yahoo's 'no data' answers."""
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise ScanError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise ScanError("no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    return series_from_result(res[0])


def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _norm_symbol(sym):
    s = ins.from_yahoo(sym)
    return INDEX_SYMBOL if s == "NSEI" else s


def _absorb(doc, name_hint, data, errors):
    """One parsed offline JSON doc -> data / errors."""
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline v8 chart without a symbol: name the file <SYMBOL>.json")
        sym = _norm_symbol(sym)
        try:
            data[sym] = parse_chart(doc, sym)
        except ScanError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a v8 chart or a bundle" % name_hint)


def load_offline(path):
    """A folder of <T>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            if isinstance(doc, dict) and ("chart" in doc or "spark" in doc):
                _absorb(doc, "" if "spark" in doc else _stem(f), data, errors)
            else:
                _absorb(doc, "", data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


# ---------------------------------------------------------------- universe

def build_universe(equity_text, fno_text=None, index_text=None):
    """NSE files -> (rows [(SYM, name, group)], counts, notes). EQ series only; an index
    list narrows it; an F&O list sets the group (cas / other), else every group is 'any'."""
    try:
        kept, counts = ins.parse_equity_list(equity_text)
        fno = ins.parse_fno_list(fno_text) if fno_text else None
        idx = ins.parse_index_list(index_text) if index_text else None
    except ins.InstrumentError as e:
        raise ScanError(str(e))
    counts = dict(counts)
    rows, notes = [], []
    for r in kept:
        if idx is not None and r["symbol"] not in idx:
            counts["not in the index"] = counts.get("not in the index", 0) + 1
            continue
        rows.append((r["symbol"], r["name"], ins.group_of(r["symbol"], fno)))
    if idx is not None:
        missing = len(idx - {r["symbol"] for r in kept})
        if missing:
            notes.append("%d index constituents are not in the EQ list (renamed or suspended)" % missing)
    if fno is None:
        notes.append("no F&O file given: closing-auction group unknown, the earlier session end is used")
    return sorted(rows), counts, notes


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes (the same math as the reel's refs/scan.py)."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def daily_closes(series, now_utc):
    """[(date, close)] of COMPLETE daily bars: null closes dropped, today's bar dropped
    until the day is settled (closing auction included), anything dated after the clock dropped."""
    now = to_ist(now_utc)
    today = now.date()
    done_at = mk.settled_at(today)
    today_done = done_at is None or now.time() >= done_at
    out = {}
    for ts, c in zip(series["t"], series["c"]):
        if c is None:
            continue
        d = ts_to_ist(ts).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = float(c)  # one bar a day: a late duplicate (the live tick) wins
    return sorted(out.items())


def scan_daily(data, symbols, now_utc, min_price, min_turnover, groups=None):
    """Pure. data = {SYM: series}. min_turnover is rupees (price x last volume).
    Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_closes(s, now_utc)
        meta = s.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None and bars:
            price = bars[-1][1]
        vol = meta.get("regularMarketVolume")
        turnover = price * vol if price is not None and vol is not None else None
        if price is None or price < min_price or (min_turnover > 0 and (turnover is None or turnover < min_turnover)):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "group": (groups or {}).get(sym, "any"), "setups": tags,
                         "price": round(closes[-1], 2), "close_date": d_last.isoformat(),
                         "rsi": round(nums["rsi"], 1), "volume": int(vol) if vol is not None else None,
                         "turnover_cr": round(turnover / 1e7, 2) if turnover is not None else None})
    hits.sort(key=lambda h: (-(h["turnover_cr"] or 0), h["symbol"]))  # most rupees traded first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k):
    s = session(day)
    return datetime.datetime.combine(day, s[0]) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    """5-minute bars of `day` that are complete at `cutoff` (naive Indian time),
    on the grid, with a close. Sorted, one per slot."""
    s = session(day)
    if s is None:
        return []
    open_t, close_t = s
    out = {}
    os_, hs, ls = series.get("o"), series.get("h"), series.get("l")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        ist = ts_to_ist(ts)
        if ist.date() != day or ist.second or ist.minute % BAR_MIN:
            continue  # another day, or the live tick off the grid
        if ist.time() < open_t or ist.time() >= close_t:
            continue  # pre-open auction, after hours, the closing auction
        if ist + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue  # still in progress at the cutoff
        v = series.get("v")
        out[ist] = {"start": ist, "c": float(c),
                    "o": None if not os_ or os_[i] is None else float(os_[i]),
                    "h": None if not hs or hs[i] is None else float(hs[i]),
                    "l": None if not ls or ls[i] is None else float(ls[i]),
                    "v": (v[i] or 0) if v else 0}
    return [out[k] for k in sorted(out)]


def usable(bars, day, cutoff, n_range):
    """Every slot from the open to the cutoff is there, and at least the range."""
    s = session(day)
    if s is None:
        return False
    want = int((cutoff - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    want = min(want, int((datetime.datetime.combine(day, s[1]) - slot(day, 0)).total_seconds() // (BAR_MIN * 60)))
    if want < n_range or len(bars) != want:
        return False
    return all(b["start"] == slot(day, k) for k, b in enumerate(bars))


def orb_candidate(bars, n_range):
    """Stage 1, closes only: some completed bar after the range closed at or above
    the highest close of the range. Necessary for a breakout (range high >= any range close)."""
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def orb_hit(bars, n_range):
    """Stage 2, real highs and lows: the first completed bar to CLOSE above the range high."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round(b["c"], 2), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
    return None


# signals: shown beside a hit, never used to filter it

def vwap_through(bars):
    num = sum(((b["h"] if b["h"] is not None else b["c"]) + (b["l"] if b["l"] is not None else b["c"]) + b["c"])
              / 3.0 * b["v"] for b in bars)
    den = sum(b["v"] for b in bars)
    return num / den if den else None


def move_pct(bars):
    """Percent from the first bar's open (its close if the open is missing) to the last close."""
    if not bars:
        return None
    first = bars[0]["o"] if bars[0]["o"] is not None else bars[0]["c"]
    return (bars[-1]["c"] / first - 1) * 100 if first else None


def prior_sessions(series, day):
    """{date: bars} for the earlier sessions in the chart that hold at least an hour of bars."""
    out = {}
    for d in sorted({ts_to_ist(t).date() for t in series["t"]}):
        s = session(d) if d < day else None
        if s is None:
            continue
        bars = session_bars(series, d, datetime.datetime.combine(d, s[1]))
        if len(bars) >= 12:
            out[d] = bars
    return out


def signals_for(full, hit, series, day, index_bars=None):
    k = hit["bar_index"]
    upto = full[:k + 1]
    vwap = vwap_through(upto)
    sig = {"vwap": round(vwap, 2) if vwap is not None else None,
           "above_vwap": (hit["price"] > vwap) if vwap is not None else None,
           "rel_volume": None, "prev_day_high": None, "above_prev_day_high": None, "vs_nifty_pp": None}
    prior = prior_sessions(series, day)
    sums = [sum(b["v"] for b in bars[:k + 1]) for bars in prior.values() if len(bars) > k]
    sums = [x for x in sums if x > 0]
    today_v = sum(b["v"] for b in upto)
    if len(sums) >= 2 and today_v:
        sig["rel_volume"] = round(today_v / (sum(sums) / len(sums)), 2)
    if prior:
        last_day = max(prior)
        hi = max(b["h"] if b["h"] is not None else b["c"] for b in prior[last_day])
        sig["prev_day_high"] = round(hi, 2)
        sig["above_prev_day_high"] = hit["price"] > hi
    mv = move_pct(upto)
    if index_bars is not None and mv is not None and len(index_bars) > k:
        sig["vs_nifty_pp"] = round(mv - move_pct(index_bars[:k + 1]), 2)   # both through the trigger bar
    return sig


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    """-> (day, cutoff naive IST) or raise ScanError with the plain message."""
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No session on %s (%s): the market is closed. Run orb on a trading day "
                        "after the opening range, or replay a past day with --offline." % (day_label(day), why))
    s = session(day)
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, s[1])
    if until is None:
        until = mk.no_entries_after(day)
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s IST)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after continuous trading ends at %s on %s."
                            % (at.strftime("%H:%M"), clock_label(close_dt), day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("Continuous trading ended at %s IST on %s. Replay the session with --at 10:15."
                            % (clock_label(close_dt), day_label(day)))
    if cutoff < first_close:
        raise ScanError("It is %s IST: the %d-minute opening range runs %s to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(slot(day, 0)), clock_label(range_end),
                           clock_label(first_close), clock_label(first_close)))
    if cutoff.time() > until:
        raise ScanError("It is %s IST, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb tomorrow after %s, or replay "
                        "today with --at 10:15." % (clock_label(cutoff), until.strftime("%H:%M"),
                                                      clock_label(first_close)))
    return day, cutoff


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_turnover=0.0, confirm=None,
             max_confirm=None, groups=None, index_series=None):
    """stage1 = {SYM: series} with closes (spark or v8). confirm(sym) -> series with highs/lows
    (None = use the stage-1 series). With confirm and max_confirm, only the max_confirm most
    traded candidates (rupees traded so far) are confirmed; the rest are counted as unconfirmed.
    Returns (hits, counts, failed {sym: why})."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0, "bad_data": 0}
    index_bars = None
    if index_series:
        ib = session_bars(index_series, day, cutoff)
        if usable(ib, day, cutoff, n_range):
            index_bars = ib
    counts["index_available"] = index_bars is not None
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        if s.get("v"):
            turnover = sum(b["c"] * b["v"] for b in bars)
        else:
            turnover = bars[-1]["c"] * ((s.get("meta") or {}).get("regularMarketVolume") or 0)
        if bars[-1]["c"] < min_price or turnover < min_turnover:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n_range):
            cands.append((sym, bars, turnover))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, turnover in cands:
        full, series2 = bars, stage1[sym]
        if confirm is not None:
            try:
                series2 = confirm(sym)
            except ScanError as e:
                failed[sym] = str(e)
                continue
            full = session_bars(series2, day, cutoff)
            if not usable(full, day, cutoff, n_range):
                failed[sym] = "the v8 chart has a missing bar"
                continue
            ts_bad = quality.timestamp_problems(series2["t"])
            if ts_bad["out_of_order"]:
                counts["bad_data"] += 1
                failed[sym] = "bad data: candles out of order"
                continue
        bad = quality.bar_problems(full)
        if bad:
            counts["bad_data"] += 1
            failed[sym] = "bad data: %s at %s" % (bad[0][1], bad[0][0].strftime("%H:%M"))
            continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range)
        if h:
            sig = signals_for(full, h, series2, day, index_bars)
            sig["turnover_cr"] = round(turnover / 1e7, 2)
            hits.append(dict({"symbol": sym, "group": (groups or {}).get(sym, "any"), "setups": ["orb"]},
                             **dict(h, signals=sig)))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- fetching

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def fetch_spark(symbols, rng, interval, pause, a=None):
    """Batches of 20. -> ({SYM: series}, {SYM: error})."""
    data, failed = {}, {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            SLEEP(pause)
        try:
            got = parse_spark(yahoo_get(spark_path(batch, rng, interval)))
        except (ScanError, ValueError) as e:
            for s in batch:
                failed[s] = str(e)
            continue
        for s in batch:
            if s in got:
                data[s] = got[s]
            else:
                failed[s] = "no data from Yahoo"
        if a is not None and len(batches) > 25 and (k + 1) % 50 == 0:
            progress(a, "  spark %d of %d requests" % (k + 1, len(batches)))
    return data, failed


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(s for s in offline_syms if not s.startswith("^"))  # offline: every chart, but not the index
    else:
        path = a.universe or DEFAULT_UNIVERSE
        if not os.path.exists(path):
            raise ScanError("no %s yet: run `scan.py universe` first, or pass --symbols" % path)
        syms = symbols_from_file(path)
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def groups_from_file(path):
    """SYMBOL|Name|group lines -> {SYMBOL: group}. Anything else is left out (group 'any')."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        return out
    for ln in text.splitlines():
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) >= 3 and parts[0] and not ln.lstrip().startswith("#") and parts[2] in ("cas", "other"):
            out[parts[0].upper()] = parts[2]
    return out


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln.split("|")[0].strip().upper())
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def money(x):
    from _common import in_fmt as fmt
    return fmt.inr(x)


# ---------------------------------------------------------------- commands

def _read_text(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _download(url, what, flag):
    try:
        return http_get(url, timeout=60)
    except urllib.error.HTTPError as e:
        why = "HTTP %d" % e.code
    except (urllib.error.URLError, OSError) as e:
        why = str(getattr(e, "reason", e))
    raise ScanError("could not download %s from NSE (%s). Download it from NSE's website by hand and "
                    "pass it with %s." % (what, why, flag))


def cmd_universe(a):
    t0 = time.time()
    equity = _read_text(a.equity_file) if a.equity_file else _download(NSE_EQUITY_URL, "the equity list", "--equity-file")
    fno = None
    if a.fno_file:
        fno = _read_text(a.fno_file)
    elif not a.equity_file and not a.no_fno:
        if a.pause:
            SLEEP(a.pause)
        try:
            fno = http_get(NSE_FNO_URL, timeout=60)
        except (urllib.error.URLError, OSError):
            fno = None  # the group stays unknown; build_universe says so
    index = _read_text(a.index_file) if a.index_file else None
    rows, counts, notes = build_universe(equity, fno, index)
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write("# SYMBOL|Name|group  (cas = closing auction session stocks, other, any = unknown)\n")
            for sym, name, group in rows:
                fh.write("%s|%s|%s\n" % (sym, name, group))
    dropped = {k: v for k, v in counts.items() if k not in ("EQ", "rows")}
    groups = {}
    for _, _, g in rows:
        groups[g] = groups.get(g, 0) + 1
    if a.json:
        print(json.dumps({"count": len(rows), "rows": counts.get("rows", 0), "dropped": dropped, "groups": groups,
                          "notes": notes, "out": None if a.no_write else a.out,
                          "seconds": round(time.time() - t0, 1)}, indent=2))
    else:
        print("NSE equity list: %s rows." % "{:,}".format(counts.get("rows", 0)))
        print("Kept %s EQ-series stocks%s." % ("{:,}".format(len(rows)), " in the index list" if a.index_file else ""))
        if dropped:
            print("Dropped: " + ", ".join("%s %s" % ("{:,}".format(v), k) for k, v in
                                          sorted(dropped.items(), key=lambda kv: -kv[1])) + ".")
        if "cas" in groups or "other" in groups:
            print("Closing-auction group: %s cas, %s other." % (groups.get("cas", 0), groups.get("other", 0)))
        for n in notes:
            print("Note: " + n + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name|group a line)." % a.out)
    return 0 if rows else 1


def cmd_daily(a):
    t0 = time.time()
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    groups = groups_from_file(a.universe or DEFAULT_UNIVERSE)
    if not a.offline:
        progress(a, "Fetching daily closes for %s symbols (%d spark requests)..."
                 % ("{:,}".format(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, "3mo", "1d", a.pause, a)
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_turnover_cr * 1e7, groups)
    nfailed = len(symbols) - counts["fetched"]
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    doc = {"created_by": "trader-scanner", "schema_version": 2, "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mode": "daily", "exchange": "NSE", "currency": "INR", "timezone": "Asia/Kolkata",
           "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"],
           "succeeded": counts["fetched"], "failed": nfailed, "partial": nfailed > 0,
           "source": "yahoo", "filters": {"min_price": a.min_price, "min_turnover_cr": a.min_turnover_cr},
           "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    local = to_ist(now)
    print("Daily scan, %s IST, closes through %s (Yahoo spark, daily closes only)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing"))
    print("Fetched %s of %s charts%s. %s passed the liquidity filter (%s+ and %s crore+ traded)%s; "
          "%s scanned." % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
                           (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
                           "{:,}".format(counts["fetched"] - counts["illiquid"]), money(a.min_price),
                           _n(a.min_turnover_cr),
                           (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
                           "{:,}".format(counts["scanned"])))
    if nfailed:
        print("PARTIAL: %s symbols had no usable data, so this list may be missing names." % "{:,}".format(nfailed))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most rupees traded first:")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-12s %14s  RSI %5.1f  %s" % (h["symbol"], money(h["price"]), h["rsi"],
                                              ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL.")
    return 0 if hits else 1


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def _sig_text(sig):
    bits = []
    if sig.get("above_vwap") is not None:
        bits.append("above VWAP" if sig["above_vwap"] else "below VWAP")
    if sig.get("rel_volume") is not None:
        bits.append("%.1fx volume" % sig["rel_volume"])
    if sig.get("above_prev_day_high") is not None:
        bits.append("over prev-day high" if sig["above_prev_day_high"] else "under prev-day high")
    if sig.get("vs_nifty_pp") is not None:
        bits.append("%+.1f pp vs Nifty" % sig["vs_nifty_pp"])
    return ", ".join(bits)


def cmd_orb(a):
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until_text = a.until or cfg.get("no_entries_after")
    until = parse_hhmm(until_text, "--until") if until_text else None   # None: the session's own default
    data = failed = None
    explicit_day = None
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [ts_to_ist(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise ScanError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    groups = groups_from_file(a.universe or DEFAULT_UNIVERSE)
    confirm = None
    index_series = None
    if not a.offline:
        today = to_ist(parse_now(None)).date()
        if (today - day).days > 6:
            raise ScanError("Yahoo's free 5-minute history is short: replay %s with --offline" % day.isoformat())
        progress(a, "Stage 1: 5-minute closes for %s symbols (%d spark requests)..."
                 % ("{:,}".format(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, "1d" if day == today else "5d", "%dm" % BAR_MIN, a.pause, a)
        done = []

        def confirm(sym):
            if a.pause:
                SLEEP(a.pause)
            done.append(sym)
            if len(done) % 50 == 0:
                progress(a, "  stage 2: %d v8 charts so far" % len(done))
            return parse_chart(yahoo_get(chart_path(sym, "5d", "%dm" % BAR_MIN)), sym)
        try:
            index_series = parse_chart(yahoo_get(chart_path(INDEX_SYMBOL, "5d", "%dm" % BAR_MIN)), INDEX_SYMBOL)
        except (ScanError, ValueError):
            index_series = None   # the vs_nifty_pp signal is simply left out
    else:
        index_series = data.get(INDEX_SYMBOL)
    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price,
                                     a.min_turnover_cr * 1e7, confirm, a.max_confirm, groups, index_series)
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    as_of = "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M"))
    partial = bool(counts["unconfirmed"] or nfailed)
    doc = {"created_by": "trader-scanner", "schema_version": 2, "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mode": "orb", "exchange": "NSE", "currency": "INR", "timezone": "Asia/Kolkata",
           "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"],
           "succeeded": counts["fetched"], "failed": nfailed, "unconfirmed": counts["unconfirmed"],
           "partial": partial, "source": "yahoo", "or_minutes": a.or_minutes,
           "interval": "%dm" % BAR_MIN, "index_available": counts["index_available"],
           "filters": {"min_price": a.min_price, "min_turnover_cr": a.min_turnover_cr}, "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    print("Opening-range breakout scan, %s %s IST: %d-minute range (%s to %s), 5-minute bars, Yahoo."
          % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(slot(day, 0)),
             clock_label(slot(day, a.or_minutes // BAR_MIN))))
    print("Fetched %s of %s charts%s; %s usable%s; %s candidates, %s confirmed with real highs and lows."
          % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
             (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
             "{:,}".format(counts["scanned"]),
             (" (%s under the price / turnover floor)" % "{:,}".format(counts["illiquid"])) if counts["illiquid"] else "",
             "{:,}".format(counts["candidates"]), "{:,}".format(counts["confirmed"])))
    if counts["bad_data"]:
        print("%d chart%s dropped for bad data (impossible prices or out-of-order candles)."
              % (counts["bad_data"], "" if counts["bad_data"] == 1 else "s"))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded are; --max-confirm 0 "
              "checks all)." % ("{:,}".format(counts["unconfirmed"]), "{:,}".format(a.max_confirm)))
    elif nfailed:
        print("PARTIAL: %s symbols had no usable data, so this list may be missing names." % "{:,}".format(nfailed))
    if not counts["index_available"]:
        print("Nifty 50 chart not available: the vs-Nifty signal is left out.")
    if hits:
        print("%d of %s charts closed above their opening-range high by %s:"
              % (len(hits), ("the %s checked" % "{:,}".format(counts["confirmed"])) if counts["unconfirmed"]
                 else "{:,}".format(counts["scanned"]), clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %8s  %-12s %14s  range %s to %s  %+.2f%% over" % (
                h["at"], h["symbol"], money(h["price"]), _p(h["or_low"]), _p(h["or_high"]), h["pct_over"]))
            st = _sig_text(h["signals"])
            if st:
                print("            " + st)
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL and /trader-entry SYMBOL.")
    return 0 if hits else 1


def _p(x):
    return "{:,.2f}".format(x)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (trader-scanner): daily setups and opening-range "
                                             "breakouts on NSE stocks.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="NSE equity list -> trader/universe.txt")
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--equity-file", metavar="FILE", help="NSE's EQUITY_L.csv (default: download it)")
    p.add_argument("--index-file", metavar="FILE", help="an NSE index constituent list, e.g. Nifty 500: keep only those")
    p.add_argument("--fno-file", metavar="FILE", help="NSE's F&O lot-size file: marks the closing-auction group")
    p.add_argument("--no-fno", action="store_true", help="do not try to download the F&O file")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--pause", type=float, default=PAUSE)
    p.add_argument("--json", action="store_true")

    def common(p, min_price, min_turnover_cr):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of symbols")
        p.add_argument("--universe", help="symbol list, one SYMBOL|Name|group a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price, help="rupees (%s)" % _n(min_price))
        p.add_argument("--min-turnover-cr", type=float, default=min_turnover_cr,
                       help="crore rupees traded (%s)" % _n(min_turnover_cr))
        p.add_argument("--offline", metavar="PATH", help="fixture folder or file instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-11-23 10:15" Indian time, or ...Z)')
        p.add_argument("--out", default="trader/watchlist.json")
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (%.1f)" % PAUSE)
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="pre-market setups from daily closes")
    common(p, DAILY_MIN_PRICE, DAILY_MIN_TURNOVER_CR)
    p = sub.add_parser("orb", help="opening-range breakouts so far today")
    common(p, ORB_MIN_PRICE, ORB_MIN_TURNOVER_CR)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="v8 charts to fetch in stage 2, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this Indian time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM (default: config no_entries_after, else 30 minutes "
                                   "before continuous trading ends)")
    p.add_argument("--config", default="trader/config.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except ScanError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
