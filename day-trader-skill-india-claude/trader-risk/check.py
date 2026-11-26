#!/usr/bin/env python3
"""Pick sanity check for trader-risk. Standard library only, no network.

Reads the sized pick (trader/sizing.json from trader-budget) and checks it
against your rules (trader/config.json) and, if you have one, a fresh price
(trader/prices.json). The answer is PASS or BLOCK with the reasons. It places
nothing and produces no order: a BLOCK means do not take this pick as it stands.

    python3 check.py                                 # trader/sizing.json, writes trader/risk.json
    python3 check.py --now "2026-11-23 10:15"        # pin the clock (Indian time)
    python3 check.py --json                          # the verdict as JSON
    python3 check.py --no-write                      # check without saving the verdict

The numbers are worked out again from the pick's own entry, stop, target and
quantity, not copied from the sizing file, so a file edited by hand or left over
from an older price cannot slip through.

What blocks a pick:

  pick        a missing or impossible symbol, side, quantity, entry, stop or target;
              anything but a long (buy); a symbol on your "blocked" list
  today       the pick is for another day
  window      the NSE is closed (weekend, holiday, outside continuous trading,
              the pre-open or closing auction), or it is before no_entries_before
              or at / after no_entries_after (by default 30 minutes before
              continuous trading ends for the stock's group), or past flat-by
  stop        no stop (a pick always needs one: there is no setting to turn that off), a stop
              at or above the entry, a target at or below it
  size        under min_order_inr, over max_order_inr, over max_position_pct of the
              budget, or more than your cash after the buffer (no leverage is assumed)
  loss        the ALL-IN loss at the stop (price move, round-trip charges, slippage
              on both fills) over max_loss_per_trade_inr. The entry is the HIGHER of
              the pick's entry and a fresh price (under 15 minutes old), so a stale
              pick cannot understate the risk
  reward:risk (target - entry) / (entry - stop) under min_reward_risk
  costs       charges and slippage take more than max_cost_pct_of_reward percent of
              the gain at the target (25 by default): a flat per-order brokerage
              makes small trades poor value, and this says so
  price       a fresh price already at or under the stop, or at or over the target

Warnings (never block): a price that is missing or too old to check against; a
signal older than max_signal_age_minutes (30); placeholder charge rates; a year
the holiday list does not cover.

NOT CHECKED, because no free data feeds it here: price bands and circuit limits
(a stop cannot fill at a lower circuit), surveillance and trade-for-trade lists,
bid-ask spread and market depth, margin and broker risk checks. The output lists
these every time. Check them in your own broker app before you act.

Exit code 0 = PASS, 1 = BLOCK or an error.
Education and tooling, not financial advice.
"""

import argparse
import datetime
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import in_costs as costs  # noqa: E402
from _common import in_fmt as fmt  # noqa: E402
from _common import in_instruments as ins  # noqa: E402
from _common import in_market as mk  # noqa: E402

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 75000,
    "min_order_inr": 1000, "max_loss_per_trade_inr": 500, "min_reward_risk": 1.0,
    "max_cost_pct_of_reward": 25, "slippage_pct": 0.05, "max_signal_age_minutes": 30,
    "no_entries_before": "09:30", "no_entries_after": None, "flat_by": None,
    "blocked": [], "allow_short": False,
}
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
FRESH_MINUTES = 15
EPS = 1e-9
NOT_CHECKED = ("price bands and circuit limits (a stop cannot fill at a lower circuit)",
               "surveillance and trade-for-trade lists",
               "bid-ask spread and market depth",
               "margin and your broker's own risk checks")
SAFETY = "Education and tooling, not financial advice."


class RiskError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as Indian time."""
    return mk.parse_now(text, RiskError)


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def read_json(path, what, required=True):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def hhmm(value, key):
    return mk.parse_hhmm(str(value), key, RiskError)


def money(x):
    x = float(x)
    return fmt.inr(x, 0 if abs(x - round(x)) < 0.005 else 2)


def _p(x):
    return "%.2f" % float(x)


def _short(x, places=1):
    t = ("%.*f" % (places, float(x))).rstrip("0").rstrip(".")
    return t or "0"


def fresh_price(doc, sym, now_utc, minutes=FRESH_MINUTES):
    """(price, time_utc, fresh) for sym from a prices.json doc, or (None, None, False)."""
    if not isinstance(doc, dict):
        return None, None, False
    p = _f((doc.get("prices") or {}).get(sym))
    if p is None or p <= 0:
        return None, None, False
    det = ((doc.get("detail") or {}).get(sym)) or {}
    t = parse_utc(det.get("time")) or parse_utc(det.get("fetched")) or parse_utc(doc.get("as_of"))
    if t is None:
        return p, None, False
    age = (now_utc - t).total_seconds()
    return p, t, -60 <= age <= minutes * 60


def parse_clock(text):
    """'9:45 AM' -> time, or None."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$", str(text or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return datetime.time(h, mi) if h < 24 and mi < 60 else None


# ---------------------------------------------------------------- the checks

def row(check, status, detail):
    return {"check": check, "status": status, "detail": detail}


def window_row(now_ist, group, c, flat_by_text=None):
    """Is a new entry allowed right now? Returns one row."""
    day = now_ist.date()
    ok, why = mk.trading_day(day)
    if not ok:
        return row("window", "block", "the NSE is closed today (%s)" % why)
    state, detail = mk.state(now_ist, group)
    s = mk.session(day, group)
    close_dt = datetime.datetime.combine(day, s[1])
    if state == "pre-open":
        return row("window", "block", "the market is in the pre-open auction; continuous trading starts at %s"
                   % mk.clock_label(datetime.datetime.combine(day, s[0])))
    if state == "closing-auction":
        return row("window", "block", "the closing auction session is running: continuous trading ended at %s for "
                   "these stocks" % mk.clock_label(close_dt))
    if state == "closed":
        return row("window", "block", "the market is closed (continuous trading is %s to %s IST)"
                   % (mk.clock_label(datetime.datetime.combine(day, s[0])), mk.clock_label(close_dt)))
    before = hhmm(c["no_entries_before"], "no_entries_before")
    after_text = c.get("no_entries_after")
    after = hhmm(after_text, "no_entries_after") if after_text else mk.no_entries_after(day, group)
    after = min(after, s[1])
    flat = hhmm(flat_by_text or c.get("flat_by"), "flat_by") if (flat_by_text or c.get("flat_by")) \
        else mk.flat_by(day, group)
    t = now_ist.time()
    if t < before:
        return row("window", "block", "it is %s IST, before no_entries_before %s" % (mk.clock_label(now_ist), before.strftime("%H:%M")))
    if t >= after:
        return row("window", "block", "it is %s IST, at or after %s, the last time for a new entry"
                   % (mk.clock_label(now_ist), after.strftime("%H:%M")))
    if t >= flat:
        return row("window", "block", "it is %s IST, at or after the flat-by time %s" % (mk.clock_label(now_ist), flat.strftime("%H:%M")))
    return row("window", "pass", "open; entries until %s IST, flat by %s IST" % (after.strftime("%H:%M"), flat.strftime("%H:%M")))


def check(pick, cfg, prices=None, now_utc=None, charges=None):
    """Pure function: one sized pick + your rules -> the verdict dict."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    now_ist = mk.to_ist(now_utc)
    rows = []
    res = {"symbol": None, "verdict": "BLOCK", "checks": rows, "blocks": [], "warnings": [],
           "not_checked": list(NOT_CHECKED)}
    if not isinstance(pick, dict):
        raise RiskError("the sizing is not a JSON object")
    sym = ins.from_yahoo(str(pick.get("symbol") or "").strip())
    res["symbol"] = sym or None
    side = str(pick.get("side") or "").strip().lower()
    qty = pick.get("qty")
    entry, stop, target = _f(pick.get("entry")), _f(pick.get("stop")), _f(pick.get("target"))
    tick = _f(pick.get("tick")) or ins.DEFAULT_TICK
    group = pick.get("group") if pick.get("group") in mk.GROUPS else "any"

    # ---- pick: the facts must be there and sane
    bad = []
    if not sym or not SYMBOL.match(sym):
        bad.append("the symbol %r is not an NSE stock symbol" % pick.get("symbol"))
    if side != "buy":
        bad.append("this tool checks longs (buy) only; the pick says %r" % (pick.get("side") or "no side"))
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        bad.append("the quantity must be a whole number of shares above 0")
    for name, v in (("entry", entry), ("stop", stop), ("target", target)):
        if v is None or v <= 0:
            bad.append("the %s must be above 0" % name)
    if sym and sym in {str(b).upper() for b in (c.get("blocked") or [])}:
        bad.append("%s is on your blocked list" % sym)
    if bad:
        rows.append(row("pick", "block", "; ".join(bad)))
        return _finish(res, c, now_ist)
    rows.append(row("pick", "pass", "%s buy %d share%s, entry %s, stop %s, target %s" % (
        sym, qty, "" if qty == 1 else "s", _p(entry), _p(stop), _p(target))))

    # ---- today
    today = now_ist.date().isoformat()
    if pick.get("date") and str(pick["date"]) != today:
        rows.append(row("today", "block", "the pick is for %s and today is %s: make a new plan" % (pick["date"], today)))
    else:
        rows.append(row("today", "pass", "the pick is for today (%s)" % today))

    # ---- window
    rows.append(window_row(now_ist, group, c, pick.get("flat_by")))

    # ---- stop
    if stop >= entry:
        rows.append(row("stop", "block", "the stop %s is at or above the entry %s: a long's stop goes underneath" % (_p(stop), _p(entry))))
    elif target <= entry:
        rows.append(row("stop", "block", "the target %s is at or below the entry %s" % (_p(target), _p(entry))))
    else:
        rows.append(row("stop", "pass", "stop %s under the entry, target %s over it" % (_p(stop), _p(target))))
    if rows[-1]["status"] == "block":
        return _finish(res, c, now_ist)

    # ---- price: the entry is the higher of the pick's and a fresh price
    eff = ins.round_to_tick(entry, tick, "nearest")
    p, pt, fresh = fresh_price(prices, sym, now_utc) if prices is not None else (None, None, False)
    if p is None:
        rows.append(row("price", "warn", "no price to check against: checked on the pick's own entry %s only. A delayed "
                        "price can lag, so look at the live price in your own app" % _p(entry)))
    elif not fresh:
        rows.append(row("price", "warn", "the price %s is %s: too old to use, checked on the pick's entry only"
                        % (_p(p), ("from " + mk.to_ist(pt).strftime("%Y-%m-%d %H:%M") + " IST") if pt else "undated")))
    else:
        pr = ins.round_to_tick(p, tick, "nearest")
        if pr <= stop:
            rows.append(row("price", "block", "the latest price %s (as of %s IST) is already at or under the stop %s"
                            % (_p(pr), mk.to_ist(pt).strftime("%H:%M"), _p(stop))))
        elif pr >= target:
            rows.append(row("price", "block", "the latest price %s (as of %s IST) is already at or over the target %s: "
                            "the move has happened" % (_p(pr), mk.to_ist(pt).strftime("%H:%M"), _p(target))))
        elif pr > eff:
            eff = pr
            rows.append(row("price", "warn", "the latest price %s (as of %s IST) is above the pick's entry %s: the risk "
                            "is checked at %s" % (_p(pr), mk.to_ist(pt).strftime("%H:%M"), _p(entry), _p(pr))))
        else:
            rows.append(row("price", "pass", "the latest price %s (as of %s IST) is inside the stop and target"
                            % (_p(pr), mk.to_ist(pt).strftime("%H:%M"))))

    # ---- size
    budget = _f(c.get("budget")) or 0.0
    value = qty * eff
    why = []
    if c.get("min_order_inr") and value < float(c["min_order_inr"]) - 0.005:
        why.append("%s is under your %s minimum order" % (money(value), money(c["min_order_inr"])))
    if c.get("max_order_inr") and value > float(c["max_order_inr"]) + 0.005:
        why.append("%s is over your %s max order" % (money(value), money(c["max_order_inr"])))
    if budget > 0:
        cap = budget * float(c.get("max_position_pct") or 100) / 100.0
        if value > cap + 0.005:
            why.append("%s is over %s%% of your %s budget (%s)" % (money(value), _short(c.get("max_position_pct") or 100), money(budget), money(cap)))
        cash = budget - budget * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value > cash + 0.005:
            why.append("%s is more than your cash after the %s%% buffer (%s): no leverage is assumed"
                       % (money(value), _short(c.get("cash_buffer_pct") or 0), money(cash)))
    else:
        why.append("no budget in the config: set \"budget\"")
    rows.append(row("size", "block" if why else "pass", "; ".join(why) if why else
                    "%s is inside your order, position and cash limits" % money(value)))

    # ---- loss, reward:risk, costs: worked out again here
    slip = float(c.get("slippage_pct") or 0)
    try:
        charges = charges or costs.load()
        net = costs.net_outcomes(eff, stop, target, qty, slip, charges)
    except costs.CostError as e:
        raise RiskError(str(e))
    max_loss = _f(c.get("max_loss_per_trade_inr"))
    loss = float(net["loss_at_stop"])
    price_loss = qty * (eff - stop)
    if max_loss and loss > max_loss + 0.005:
        rows.append(row("loss", "block", "the loss at the stop is %s all-in (%s of price move plus charges and slippage), "
                        "over your %s max loss per trade" % (money(loss), money(price_loss), money(max_loss))))
    else:
        rows.append(row("loss", "pass", "the loss at the stop is %s all-in, inside your %s limit" % (
            money(loss), money(max_loss) if max_loss else "(no limit set)")))
    rr = (target - eff) / (eff - stop)
    mrr = _f(c.get("min_reward_risk"))
    if mrr and rr < mrr - EPS:
        rows.append(row("reward:risk", "block", "reward:risk %.2f is under your minimum %s" % (rr, _short(mrr, 2))))
    else:
        rows.append(row("reward:risk", "pass", "reward:risk %.2f before costs, %s after" % (
            rr, ("%.2f" % float(net["net_reward_risk"])) if net["net_reward_risk"] is not None else "n/a")))
    gross = qty * (target - eff)
    gain = float(net["gain_at_target"])
    share = 100.0 * (gross - gain) / gross if gross > 0 else None
    cap_pct = _f(c.get("max_cost_pct_of_reward"))
    if share is not None and cap_pct and share > cap_pct + EPS:
        rows.append(row("costs", "block", "charges and slippage take %s%% of the reward at the target (you allow %s%%): "
                        "%s gross becomes %s net. A small position pays the same flat brokerage as a big one"
                        % (_short(share), _short(cap_pct), money(gross), money(gain))))
    else:
        rows.append(row("costs", "pass", "charges and slippage take %s%% of the reward (limit %s%%)" % (
            _short(share or 0), _short(cap_pct or 0))))
    if not net["verified"]:
        rows.append(row("charges", "warn", "the charge rates are placeholders, not checked against any broker's schedule: "
                        "put yours in trader/charges.json"))

    # ---- stale signal
    et = parse_clock(pick.get("entry_time"))
    if et and c.get("max_signal_age_minutes") and str(pick.get("date")) == today:
        age = (now_ist - datetime.datetime.combine(now_ist.date(), et)).total_seconds() / 60.0
        if age > float(c["max_signal_age_minutes"]):
            rows.append(row("signal", "warn", "the signal was %d minutes ago (%s): a late entry is a different trade"
                            % (int(age), pick["entry_time"])))
    if not mk.calendar_known(now_ist.date()):
        rows.append(row("calendar", "warn", "the holiday list covers %s only: check nseindia.com that today is a trading day"
                        % " and ".join(map(str, mk.calendar_years()))))
    return _finish(res, c, now_ist, net=net, eff=eff)


def _finish(res, c, now_ist, net=None, eff=None):
    res["blocks"] = ["%s: %s" % (r["check"], r["detail"]) for r in res["checks"] if r["status"] == "block"]
    res["warnings"] = ["%s: %s" % (r["check"], r["detail"]) for r in res["checks"] if r["status"] == "warn"]
    res["verdict"] = "BLOCK" if res["blocks"] else "PASS"
    res["checked_at_ist"] = now_ist.strftime("%Y-%m-%d %H:%M:%S")
    if eff is not None:
        res["entry_checked_at"] = eff
    return res


def report(res):
    lines = ["Risk check for %s, %s IST: %s" % (res["symbol"] or "?", res["checked_at_ist"][:16], res["verdict"])]
    for r in res["checks"]:
        lines.append("  %-5s %-12s %s" % (r["status"].upper(), r["check"], r["detail"]))
    lines.append("")
    if res["verdict"] == "BLOCK":
        lines.append("BLOCK: do not take this pick as it stands. Fix what is blocked, or skip it.")
    else:
        lines.append("PASS: nothing in your rules stops this pick. That is not a reason to take it.")
    lines.append("Not checked here (no free data): " + "; ".join(res["not_checked"]) + ". Look at these in your own app.")
    lines.append("Nothing was placed: this tool only checks.")
    lines.append(SAFETY)
    return "\n".join(lines)


def verdict_doc(res, pick, now_utc, source=""):
    return {"created_by": "trader-risk", "schema_version": 2, "source": source, "exchange": "NSE",
            "currency": "INR", "timezone": "Asia/Kolkata",
            "created_at": now_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checked_at_ist": res["checked_at_ist"], "symbol": res["symbol"], "verdict": res["verdict"],
            "pick": {k: pick.get(k) for k in ("symbol", "side", "qty", "entry", "stop", "target", "date", "group")},
            "checks": res["checks"], "blocks": res["blocks"], "warnings": res["warnings"],
            "not_checked": res["not_checked"], "note": "A check of a pick, not an order. Nothing was placed."}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pick sanity check (trader-risk): PASS or BLOCK for one sized pick. "
                                             "It places nothing.")
    ap.add_argument("--sizing", default="trader/sizing.json", help="the sizing trader-budget wrote")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--prices", help="a prices.json (default trader/prices.json if it exists)")
    ap.add_argument("--charges", metavar="FILE", help="your broker's charges (default trader/charges.json, else the "
                                                      "bundled placeholders)")
    ap.add_argument("--out", default="trader/risk.json", help="where the verdict is saved")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--now", help='pretend it is this Indian time, e.g. "2026-11-23 10:15"')
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        pick = read_json(a.sizing, "sizing (run trader-budget --write first)")
        cfg = dict(DEFAULTS)
        cfg.update(read_json(a.config, "config", required=False) or {})
        ppath = a.prices or ("trader/prices.json" if os.path.exists("trader/prices.json") else None)
        prices = read_json(ppath, "prices") if ppath else None
        try:
            charges = costs.load(a.charges)
        except costs.CostError as e:
            raise RiskError(str(e))
        res = check(pick, cfg, prices, now, charges)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(verdict_doc(res, pick, now, a.sizing), fh, indent=2)
            fh.write("\n")
    if a.json:
        print(json.dumps({k: v for k, v in res.items()}, indent=2))
    else:
        print(report(res))
        if not a.no_write:
            print("Wrote the verdict to %s" % a.out)
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
