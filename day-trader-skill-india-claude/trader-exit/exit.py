#!/usr/bin/env python3
"""Exit Planner for trader-exit: sets the stop and the target BEFORE you
enter, so you know when to get out. Standard library only. Long side only.
It never places an order.

    python3 exit.py plan                                    # from trader/entry.json: or-mid stop, 2R target
    python3 exit.py plan --stop or-mid --target 1R --write  # saves trader/plan.json
    python3 exit.py plan --symbol RELIANCE --entry 1450.50 --or-high 1448 --or-low 1432.5
    python3 exit.py plan --stop atr:1.5 --offline RELIANCE.json   # ATR needs the session's bars
    python3 exit.py plan --symbol TCS --entry 4100 --setup level --stop pct:1
    python3 exit.py plan --qty 50 --slippage-pct 0.05       # adds the money after charges and slippage
    python3 exit.py show                                    # the current trader/plan.json

All prices are rupees on the exchange tick (Rs 0.05 for most NSE stocks; --tick
sets another). The stop rounds DOWN to a tick, away from the entry; the entry,
the opening-range levels and the target round to the nearest tick.

Stops:
  or-mid     the opening-range midpoint (the default for an orb entry)
  or-low     the opening-range low
  price:X    your price
  pct:X      X percent under the entry
  atr:K      K times the 14-bar ATR under the entry, on the session's bars up
             to the entry bar (--offline FILE or Yahoo). With fewer than 14
             bars by then it averages all of them and says so.
  A level setup has no opening-range default: give a stop.

Targets (round half up to the tick):
  2R (default), 1R, 1.5R ...   entry + R x risk, where risk = entry - stop
  price:X                      your price

--trail pct:X  adds a trailing rule to the plan: once the price has closed 1R
above your entry, keep the stop X percent under the highest close, never lower.
This tool does not watch the price: the rule is for you (or trader-alerts).

--qty N  adds what the plan is worth in rupees for N shares after the round-trip
charges and --slippage-pct (default 0.05 percent on the entry and again on the
exit): the loss if the stop is hit, the gain at the target, and the reward:risk
that is left. Brokerage is a flat amount per order, so a small position pays far
more than a large one. The charge RATES in _common/data/charges.json are
placeholders marked verified: false; put your broker's numbers in
trader/charges.json (or --charges FILE) and the plan says when they are checked.

Refused, with the reason: a stop at or above the entry, a target at or below it,
a risk under two ticks or 0.05% of the price (too tight to survive the spread),
an entry.json that has not triggered (unless you give --entry), a short, a day
the NSE is closed.

flat_by: out by then, win or lose. By default 10 minutes before continuous
trading ends for the stock's group (15:05 for the closing-auction stocks until
4 Dec 2026, 15:20 from 7 Dec; --group cas|other|any, any = the earlier end; the
group comes from entry.json). "flat_by" in trader/config.json or --flat-by
overrides it, and a time at or after the end of continuous trading is replaced
by the default with a note. "min_reward_risk" in the config (default 1.0) only
warns here; trader-risk enforces it.

A stop is a plan, not a guarantee: a gap, a fast market or a circuit limit can
fill past it, or leave you unable to sell at all.
Exit codes: plan 0 = a plan, 1 = refused or an error. show 0 = a plan exists.
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
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import in_costs as costs  # noqa: E402
from _common import in_fmt as fmt  # noqa: E402
from _common import in_instruments as ins  # noqa: E402
from _common import in_market as mk  # noqa: E402

UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0; +https://github.com/Jakeschincariol/day-trader-skill)"
BACKOFF = 5.0
HOSTS = ("query2", "query1")
INTERVALS = {"1m": 60, "5m": 300}
DEFAULTS = {"flat_by": None, "min_reward_risk": 1.0}
ATR_BARS = 14
MIN_RISK_PCT = Decimal("0.05")     # risk under 0.05% of the price (or 2 ticks, if more) is inside the spread's noise
TICK = Decimal(str(ins.DEFAULT_TICK))
PAISA = Decimal("0.01")
DEFAULT_SLIPPAGE = 0.05            # percent, a placeholder; override with --slippage-pct
EPOCH = datetime.datetime(1970, 1, 1)
NOTE = ("A stop is a plan, not a guarantee: a gap, a fast market or a circuit limit can fill past it, "
        "or leave you unable to sell.")


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- time (Indian time, from _common)

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def parse_now(text):
    """--now -> naive UTC. A time without an offset is Indian time."""
    if not text:
        return utc_now()
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (mk.to_ist(utc_now()).date().isoformat(), t.zfill(5))
    return mk.parse_now(t, PlanError)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise PlanError("%s wants an Indian time like 15:05, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def parse_clock(text, what):
    """10:15 AM, 10:15, 3:05 PM -> time."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$", str(text))
    if not m:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        if not 1 <= h <= 12:
            raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    if h > 23 or mi > 59:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    return datetime.time(h, mi)


def hm(t):
    return "%02d:%02d" % (t.hour, t.minute)


ampm = mk.clock_label


def session(d, group="any"):
    """(open_time, close_time) of continuous trading in IST, or None if closed all day."""
    return mk.session(d, group)


# ---------------------------------------------------------------- money math (Decimal, to the tick)

def D(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        s = str(x).strip()
        s = re.sub(r"^(?:rs\.?|inr|\u20b9|\$)\s*", "", s, flags=re.I)
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, ValueError):
        raise PlanError("%s is not a number: %r" % (what, x))


def rnd(x, tick=TICK):
    """Round half up to the tick."""
    t = D(tick)
    return (D(x) / t).to_integral_value(rounding=ROUND_HALF_UP) * t


def flr(x, tick=TICK):
    """A long's stop: round down to the tick, away from the entry."""
    t = D(tick)
    return (D(x) / t).to_integral_value(rounding=ROUND_FLOOR) * t


def money(x):
    return fmt.inr(D(x).quantize(PAISA))


def num(x):
    """1, 1.5, 2.25: a Decimal without trailing zeros."""
    return format(D(x).normalize(), "f")


def ratio(x):
    """reward:risk for people: 1.0, 1.5, 1.37."""
    s = "%.2f" % float(x)
    return s[:-1] if s.endswith("0") else s


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
_AMT = r"(?:rs\.?\s*|\u20b9)?"


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid", "or-midpoint"):
        return "or_mid", None
    if t in ("or-low", "or_low", "low"):
        return "or_low", None
    m = re.match(r"^(price|pct|atr):\s*" + _AMT + r"([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, price:X, pct:X or atr:K (got %r)" % text)
    kind, val = m.group(1), D(m.group(2), "--stop")
    if val <= 0:
        raise PlanError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 100:
        raise PlanError("--stop pct:X needs X under 100")
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = D(m.group(1), "--target")
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*" + _AMT + r"([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", D(m.group(1), "--target")
    raise PlanError("--target is 2R, 1R, 1.5R ... or price:X (got %r)" % text)


def parse_trail(text):
    m = re.match(r"^pct:\s*([0-9]*\.?[0-9]+)$", str(text).strip().lower())
    if not m:
        raise PlanError("--trail is pct:X (X percent under the highest close), got %r" % text)
    v = D(m.group(1), "--trail")
    if v <= 0 or v >= 100:
        raise PlanError("--trail pct:X needs X above 0 and under 100")
    return v


def r_label(r):
    n = int(r) if r == r.to_integral_value() else None
    return "%sR (%s times the risk)" % (num(r), WORDS.get(n, num(r)))


def compute(entry, stop_rule, stop_param, target_rule, target_param, or_high=None, or_low=None,
            atr=None, atr_bars=None, tick=TICK):
    """The stop, the target and the risk, in exact rupees on the tick. Raises PlanError with the reason."""
    tick = D(tick)
    if tick <= 0:
        raise PlanError("--tick must be above 0")
    e = rnd(entry, tick)
    if e <= 0:
        raise PlanError("the entry price must be above Rs 0")
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        oh, ol = rnd(or_high, tick), rnd(or_low, tick)
        if oh <= ol:
            raise PlanError("the opening-range high %s must be above its low %s" % (money(oh), money(ol)))
        if stop_rule == "or_mid":
            raw = (oh + ol) / 2
            label = "opening-range midpoint"
            detail = "(%s + %s) / 2 = Rs %s" % (money(oh), money(ol), num(raw))
        else:
            raw, label = ol, "opening-range low"
    elif stop_rule == "price":
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        raw = e * (1 - stop_param / 100)
        label = "%s%% under the entry" % num(stop_param)
        detail = "%s x (1 - %s%%) = Rs %s" % (money(e), num(stop_param), num(raw))
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs the session's bars")
        a = D(round(atr, 6))
        raw = e - stop_param * a
        label = "%s x ATR(%d) under the entry" % (num(stop_param), atr_bars)
        detail = "ATR Rs %s over the %d bars to the entry%s" % (
            num(a.quantize(Decimal("0.0001"))), atr_bars, " (fewer than 14 so far)" if atr_bars < ATR_BARS else "")
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)
    stop = flr(raw, tick)
    if stop != D(raw):
        note = "rounded down to the Rs %s tick" % num(tick)
        detail = (detail + ", " + note) if detail else "Rs %s %s" % (num(raw), note)
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                        % (money(stop), label, money(e)))
    if stop <= 0:
        raise PlanError("the stop works out to %s: pick a stop above Rs 0" % money(stop))
    risk = e - stop
    floor_risk = max(2 * tick, e * MIN_RISK_PCT / 100)
    if risk < floor_risk:
        raise PlanError("the stop is %s under the entry, less than the minimum %s (two ticks, or 0.05%% of the "
                        "price if that is more): too tight to survive the spread, normal wiggle would stop you "
                        "out. Widen it." % (money(risk), money(floor_risk.quantize(PAISA, rounding=ROUND_HALF_UP))))
    if target_rule == "r":
        target = rnd(e + target_param * risk, tick)
        t_label = r_label(target_param)
    else:
        target = rnd(target_param, tick)
        t_label = None
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a long's target goes above it"
                        % (money(target), money(e)))
    rr = ((target - e) / risk).quantize(PAISA, rounding=ROUND_HALF_UP)
    if t_label is None:
        t_label = "your price (%sR)" % ratio(rr)
    return {"entry": e, "stop": stop, "stop_label": label, "stop_detail": detail, "target": target,
            "target_r": target_param if target_rule == "r" else rr, "target_label": t_label,
            "risk": risk, "reward_risk": rr, "tick": tick}


# ---------------------------------------------------------------- bars for ATR (same reader as trader-entry)

def _open_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _sleep(seconds):
    time.sleep(seconds)


def yahoo_get(path):
    """query2 first, query1 on 429 or 5xx; one retry after a pause on 429."""
    last = "no answer"
    for host in HOSTS:
        url = "https://%s.finance.yahoo.com%s" % (host, path)
        for attempt in (0, 1):
            try:
                return _open_url(url)
            except urllib.error.HTTPError as e:
                code, body = e.code, None
                try:
                    if code == 404:     # Yahoo says why in the body: an unknown ticker
                        body = e.read().decode("utf-8", "replace")
                except Exception:
                    body = None
                finally:
                    e.close()
                if code == 404:
                    if body:
                        return body
                    raise PlanError("Yahoo has no chart at %s (HTTP 404)" % url.split("?")[0])
                last = "HTTP %d" % code
                if code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if code == 429 or code >= 500:
                    break
                raise PlanError("Yahoo answered HTTP %d for %s" % (code, url.split("?")[0]))
            except urllib.error.URLError as e:
                last = "could not connect: %s" % (e.reason,)
                break
            except OSError as e:
                last = "network error: %s" % (e,)
                break
    raise PlanError("Yahoo did not answer (%s on query2 and query1): wait a minute and try again" % last)


def read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_chart(sym, interval, day, offline=None):
    ysym = ins.plain_yahoo(sym)
    if offline:
        path = offline
        if os.path.isdir(offline):
            path = os.path.join(offline, sym + ".json")
            if not os.path.exists(path):
                path += ".gz"
        if not os.path.exists(path):
            raise PlanError("no offline file %s" % path)
        try:
            text = read_text(path)
        except (OSError, ValueError) as e:
            raise PlanError("could not read %s: %s" % (path, e))
    else:
        back = (mk.to_ist(utc_now()).date() - day).days
        rng = "1d" if back <= 0 else "5d" if back <= 6 else "1mo" if back <= 28 else "60d"
        if interval == "1m" and back > 6:
            raise PlanError("Yahoo keeps 1-minute bars for about a week")
        text = yahoo_get("/v8/finance/chart/%s?range=%s&interval=%s" % (ins.yahoo_symbol(sym), rng, interval))
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise PlanError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise PlanError("Yahoo has no data for %s: %s" % (sym, err.get("description") or "unknown symbol"))
    r = res[0] or {}
    meta = r.get("meta") or {}
    if str(meta.get("symbol") or ysym).upper() != ysym:
        raise PlanError("the chart file holds %s, not %s" % (meta.get("symbol"), sym))
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def at(key, i):
        arr = q.get(key) or []
        return arr[i] if i < len(arr) else None

    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = at("close", i)
        if t is None or c is None:
            continue
        h, lo = at("high", i), at("low", i)
        bars.append({"t": int(t), "h": float(c if h is None else h), "l": float(c if lo is None else lo),
                     "c": float(c)})
    return {"bars": bars, "data_time": meta.get("regularMarketTime")}


def session_bars(chart, day, interval, upto_slot=None, now_utc=None, group="any"):
    """Closed, on-grid bars of one NSE session, oldest first (the live tick and the auctions are dropped)."""
    step = INTERVALS[interval]
    s = session(day, group)
    if not s:
        return []
    open_dt = datetime.datetime.combine(day, s[0])
    close_dt = datetime.datetime.combine(day, s[1])
    if now_utc is None and chart.get("data_time"):
        now_utc = utc_of(chart["data_time"])
    local = mk.to_ist(now_utc) if now_utc else None
    out = {}
    for b in chart["bars"]:
        if b["t"] % step:
            continue
        ist = mk.to_ist(utc_of(b["t"]))
        if ist.date() != day or not (open_dt <= ist < close_dt):
            continue
        if local and ist + datetime.timedelta(seconds=step) > local:
            continue
        slot = int((ist - open_dt).total_seconds()) // step
        if upto_slot is not None and slot > upto_slot:
            continue
        out[slot] = b
    return [out[k] for k in sorted(out)]


def atr_of(bars, n=ATR_BARS):
    """Average true range of the last n bars (all of them if fewer). The first
    bar of the session uses its own high - low (no overnight gap)."""
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"]
        if prev is not None:
            tr = max(tr, abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


# ---------------------------------------------------------------- plan

def read_json(path, what):
    if not path or not os.path.exists(path):
        raise PlanError("no %s at %s" % (what, path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise PlanError("%s is not valid JSON: %s" % (path, e))


def read_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        doc = read_json(path, "config")
        for k in DEFAULTS:
            if doc.get(k) is not None:
                cfg[k] = doc[k]
    return cfg


def resolve_flat_by(day, group, explicit, cfg, entry_time):
    """(HH:MM, note). Default: 10 minutes before continuous trading ends for the group."""
    s = session(day, group)
    close_dt = datetime.datetime.combine(day, s[1])
    default = mk.flat_by(day, group)
    note = ""
    if explicit:
        t = parse_hhmm(explicit, "--flat-by")
        if datetime.datetime.combine(day, t) >= close_dt:
            raise PlanError("--flat-by %s is at or after the %s end of continuous trading on %s"
                            % (hm(t), ampm(close_dt), day))
    elif cfg.get("flat_by"):
        t = parse_hhmm(cfg["flat_by"], "flat_by")
        if datetime.datetime.combine(day, t) >= close_dt:
            t = default
            note = ("the flat_by %s in your config is at or after the %s end of continuous trading on %s, "
                    "so %s is used" % (cfg["flat_by"], ampm(close_dt), day.isoformat(), hm(default)))
    else:
        t = default
    if entry_time and entry_time >= t:
        raise PlanError("the entry at %s is at or after your flat-by %s: there is no time left to hold it"
                        % (ampm(entry_time), hm(t)))
    return hm(t), note


def gather(a):
    """The entry facts, from trader/entry.json or the flags."""
    if a.symbol and a.from_path:
        raise PlanError("use --from or --symbol, not both")
    if a.side in ("sell", "short"):
        raise PlanError("Longs only: no stops above a short entry. Plan a buy instead.")
    if a.symbol:
        if a.entry is None:
            raise PlanError("--symbol needs --entry PRICE")
        if (a.or_high is None) != (a.or_low is None):
            raise PlanError("give both --or-high and --or-low, or neither")
        info = {"symbol": ins.from_yahoo(a.symbol), "entry": a.entry,
                "setup": a.setup or ("orb" if a.or_high is not None else "level"),
                "date": a.date, "entry_time": a.time, "entry_bar": a.entry_bar, "interval": a.interval or "5m",
                "or_high": a.or_high, "or_low": a.or_low, "or_minutes": a.or_minutes, "group": a.group,
                "source": "flags"}
    else:
        path = a.from_path or "trader/entry.json"
        doc = read_json(path, "entry (run /trader-entry first, or give --symbol and --entry)")
        if str(doc.get("side") or "buy").lower() not in ("buy", "long"):
            raise PlanError("%s is a %s: this tool plans longs only" % (path, doc.get("side")))
        if doc.get("status") != "triggered" and a.entry is None:
            raise PlanError("%s says %s is %s, not triggered: plan once it triggers, or give --entry PRICE to plan ahead"
                            % (path, doc.get("symbol"), doc.get("status") or "unknown"))
        orr = doc.get("opening_range") or {}
        info = {"symbol": ins.from_yahoo(str(doc.get("symbol") or "")),
                "entry": a.entry if a.entry is not None else doc.get("price"),
                "setup": a.setup or doc.get("setup") or "orb", "date": a.date or doc.get("date"),
                "entry_time": a.time or (doc.get("time") if a.entry is None else None),
                "entry_bar": a.entry_bar if a.entry_bar is not None else (doc.get("bar_index") if a.entry is None else None),
                "interval": a.interval or doc.get("interval") or "5m",
                "or_high": a.or_high if a.or_high is not None else orr.get("high"),
                "or_low": a.or_low if a.or_low is not None else orr.get("low"),
                "or_minutes": orr.get("minutes") or a.or_minutes,
                "group": a.group or doc.get("group") or "any", "source": path}
        if not info["symbol"] or info["entry"] is None:
            raise PlanError("%s has no symbol or price" % path)
    if info["interval"] not in INTERVALS:
        raise PlanError("interval is 5m or 1m")
    info["group"] = info["group"] or "any"
    if info["group"] not in mk.GROUPS:
        raise PlanError("--group is cas, other or any")
    return info


def build_plan(a, now_utc):
    info = gather(a)
    cfg = read_config(a.config)
    stop_text = a.stop
    if not stop_text:
        if info["setup"] != "orb":
            raise PlanError("a %s setup has no opening-range stop to default to: give --stop price:X, pct:X or atr:K"
                            % info["setup"])
        stop_text = "or-mid"
    stop_rule, stop_param = parse_stop(stop_text)
    target_rule, target_param = parse_target(a.target or "2R")
    trail = parse_trail(a.trail) if a.trail else None
    tick = D(a.tick) if a.tick is not None else TICK
    if a.slippage_pct is not None and a.qty is None:
        raise PlanError("--slippage-pct needs --qty: the money figures are for a number of shares")
    if a.qty is not None and a.qty < 1:
        raise PlanError("--qty must be at least 1")
    if a.slippage_pct is not None and not 0 <= a.slippage_pct < 5:
        raise PlanError("--slippage-pct is a percent from 0 to under 5")
    if info["date"]:
        try:
            day = datetime.date.fromisoformat(str(info["date"]))
        except ValueError:
            raise PlanError("date wants YYYY-MM-DD, not %r" % info["date"])
    else:
        day = mk.to_ist(now_utc).date()
    ok, why = mk.trading_day(day)
    if not ok:
        raise PlanError("the NSE is closed on %s (%s): plan for a trading day with --date" % (day.isoformat(), why))
    entry_time = parse_clock(info["entry_time"], "the entry time") if info["entry_time"] else None
    atr = atr_n = None
    if stop_rule == "atr":
        chart = load_chart(info["symbol"], info["interval"], day, a.offline)
        bars = session_bars(chart, day, info["interval"], info["entry_bar"], parse_now(a.now) if a.now else None,
                            info["group"])
        if len(bars) < 3:
            raise PlanError("an atr stop needs at least 3 closed %s bars of %s on %s by the entry; found %d"
                            % (info["interval"], info["symbol"], day, len(bars)))
        atr, atr_n = atr_of(bars)
    c = compute(info["entry"], stop_rule, stop_param, target_rule, target_param, info["or_high"], info["or_low"],
                atr, atr_n, tick)
    flat_by, flat_note = resolve_flat_by(day, info["group"], a.flat_by, cfg, entry_time)
    orr = None
    if info["or_high"] is not None and info["or_low"] is not None:
        orr = {"high": float(rnd(info["or_high"], tick)), "low": float(rnd(info["or_low"], tick)),
               "minutes": int(info["or_minutes"] or 15)}
    plan = {"created_by": "trader-exit", "schema_version": 2, "symbol": info["symbol"], "exchange": "NSE",
            "currency": "INR", "timezone": "Asia/Kolkata", "group": info["group"], "side": "buy",
            "date": day.isoformat(), "setup": info["setup"], "entry": float(c["entry"]),
            "entry_time": ampm(datetime.datetime.combine(day, entry_time)) if entry_time else None,
            "entry_bar": info["entry_bar"], "interval": info["interval"], "opening_range": orr,
            "tick": float(tick), "stop": float(c["stop"]), "stop_rule": stop_rule, "stop_label": c["stop_label"],
            "target": float(c["target"]), "target_r": float(c["target_r"]), "target_label": c["target_label"],
            "risk_per_share": float(c["risk"]), "reward_risk": float(c["reward_risk"]), "flat_by": flat_by,
            "stop_detail": c["stop_detail"], "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "note": NOTE}
    if atr is not None:
        plan["atr"] = round(atr, 4)
        plan["atr_bars"] = atr_n
    if trail is not None:
        start = rnd(c["entry"] + c["risk"], tick)
        plan["trail"] = {"rule": "pct", "percent": float(trail), "starts_after_close_at": float(start),
                         "text": "once a bar closes at or above %s (1R above the entry), keep the stop %s%% under "
                                 "the highest close, and never lower it" % (money(start), num(trail))}
    warnings = []
    if flat_note:
        warnings.append("Flat-by %s: %s." % (flat_by, flat_note))
    mrr = cfg.get("min_reward_risk")
    try:
        if mrr is not None and c["reward_risk"] < D(mrr):
            warnings.append("Reward:risk %s is under your min_reward_risk %s in the config: trader-risk checks that."
                            % (ratio(c["reward_risk"]), num(D(mrr))))
    except PlanError:
        pass
    if a.qty is not None:
        try:
            ch = costs.load(a.charges)
            slip = DEFAULT_SLIPPAGE if a.slippage_pct is None else a.slippage_pct
            n = costs.net_outcomes(c["entry"], c["stop"], c["target"], a.qty, slip, ch)
        except costs.CostError as e:
            raise PlanError(str(e))
        plan["costs"] = {"qty": a.qty, "slippage_pct": slip, "loss_at_stop": float(n["loss_at_stop"]),
                         "gain_at_target": float(n["gain_at_target"]),
                         "net_reward_risk": float(n["net_reward_risk"]) if n["net_reward_risk"] is not None else None,
                         "charges_at_stop": float(n["charges_at_stop"]),
                         "charges_at_target": float(n["charges_at_target"]),
                         "charges_verified": n["verified"], "charges_file": os.path.basename(n["charges_file"] or "")}
        nrr = n["net_reward_risk"]
        if nrr is not None and mrr is not None and nrr < D(mrr):
            warnings.append("After charges and slippage the reward:risk is %s, under your min_reward_risk %s."
                            % (ratio(nrr), num(D(mrr))))
        if not n["verified"]:
            warnings.append("The charge rates are placeholders, not checked against any broker's schedule: put "
                            "yours in trader/charges.json.")
    today = mk.to_ist(now_utc).date()
    if day != today:
        warnings.append("This plan is for %s, not today (%s)." % (day.isoformat(), today.isoformat()))
    if not mk.calendar_known(day):
        warnings.append("The holiday list covers %s only: check nseindia.com that %s is a trading day."
                        % (" and ".join(map(str, mk.calendar_years())), day.isoformat()))
    return plan, warnings


def describe(plan):
    day = datetime.date.fromisoformat(plan["date"])
    setup = {"orb": "opening-range breakout", "level": "close above a level"}.get(plan["setup"], plan["setup"])
    e = D(plan["entry"])
    risk = D(plan["risk_per_share"])
    pct = (risk / e * 100).quantize(PAISA, rounding=ROUND_HALF_UP)
    when = ""
    if plan.get("entry_time"):
        when = " at %s" % plan["entry_time"]
        if plan.get("entry_bar") is not None:
            when += " (bar %d)" % plan["entry_bar"]
    out = ["Exit plan: %s long, %s, %s on %s bars" % (
        plan["symbol"], day.strftime("%a %Y-%m-%d"), setup, "5-minute" if plan["interval"] == "5m" else "1-minute")]
    out.append("  Entry         %s%s" % (money(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (money(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (money(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s (%s%% of the entry)" % (money(plan["risk_per_share"]), pct))
    out.append("  Reward:risk   %s" % ratio(plan["reward_risk"]))
    c = plan.get("costs")
    if c:
        out.append("  For %d shares, after charges and %s%% slippage on each fill:" % (c["qty"], num(D(c["slippage_pct"]))))
        out.append("    Stop hit    lose %s (charges %s)" % (money(c["loss_at_stop"]), money(c["charges_at_stop"])))
        out.append("    Target hit  gain %s (charges %s)" % (money(c["gain_at_target"]), money(c["charges_at_target"])))
        if c.get("net_reward_risk") is not None:
            out.append("    Reward:risk left %s" % ratio(c["net_reward_risk"]))
    if plan.get("trail"):
        out.append("  Trail         " + plan["trail"]["text"])
    out.append("  Flat by       %s IST, win or lose: no overnight hold" % plan["flat_by"])
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(NOTE)
    return out


def cmd_plan(a):
    now = parse_now(a.now)
    plan, warnings = build_plan(a, now)
    if a.write:
        d = os.path.dirname(a.write)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.write, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2)
            fh.write("\n")
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings), indent=2))
        return 0
    for line in describe(plan):
        print(line)
    for w in warnings:
        print("Note: " + w)
    if a.write:
        print("Wrote %s." % a.write)
    print("Next: /trader-timemachine replays it, then /trader-budget sizes it to your max loss. Nothing was placed.")
    return 0


def cmd_show(a):
    if not os.path.exists(a.plan):
        print("No plan at %s yet. Make one: exit.py plan --write" % a.plan)
        return 1
    plan = read_json(a.plan, "plan")
    for k in ("symbol", "date", "entry", "stop", "target", "risk_per_share", "reward_risk", "flat_by"):
        if plan.get(k) is None:
            raise PlanError("%s is missing %r: make a new plan with exit.py plan --write" % (a.plan, k))
    if a.json:
        print(json.dumps(plan, indent=2))
        return 0
    for line in describe(dict({"setup": "orb", "interval": "5m", "stop_label": "", "target_label": ""}, **plan)):
        print(line)
    today = mk.to_ist(parse_now(a.now)).date().isoformat()
    if plan["date"] != today:
        print("Note: this plan is for %s, not today (%s)." % (plan["date"], today))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit Planner (trader-exit): the stop and target before the entry, "
                                             "on NSE stocks.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="set the stop and target")
    p.add_argument("--from", dest="from_path", metavar="PATH", help="entry.json to plan from (default trader/entry.json)")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float, help="entry price in rupees (or a plan-ahead price with --from)")
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--setup", choices=["orb", "level"])
    p.add_argument("--date", help="session date YYYY-MM-DD (default: the entry's, or today)")
    p.add_argument("--time", help="entry time, e.g. 10:15 AM")
    p.add_argument("--entry-bar", type=int)
    p.add_argument("--interval", choices=["5m", "1m"])
    p.add_argument("--group", choices=list(mk.GROUPS), help="closing-auction group (default: from entry.json, else any)")
    p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="buy only")
    p.add_argument("--stop", help="or-mid (orb default), or-low, price:X, pct:X, atr:K")
    p.add_argument("--target", help="2R (default), 1R, 1.5R, price:X")
    p.add_argument("--tick", type=float, help="price tick in rupees (0.05)")
    p.add_argument("--trail", help="pct:X: keep the stop X percent under the highest close once 1R is reached")
    p.add_argument("--qty", type=int, help="shares: adds the money after charges and slippage")
    p.add_argument("--slippage-pct", type=float, help="percent worse than plan on each fill (0.05 with --qty)")
    p.add_argument("--charges", metavar="FILE", help="your broker's charges (default trader/charges.json, else the "
                                                     "bundled placeholders)")
    p.add_argument("--flat-by", help="HH:MM Indian time (default: 10 minutes before continuous trading ends)")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--offline", metavar="PATH", help="saved Yahoo chart JSON for an atr stop")
    p.add_argument("--now", help='pretend it is this Indian time, e.g. "2026-11-23 10:15"')
    p.add_argument("--write", nargs="?", const="trader/plan.json", metavar="PATH",
                   help="save the plan (default trader/plan.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="print the current plan")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except PlanError as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
