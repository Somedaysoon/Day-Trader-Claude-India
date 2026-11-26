#!/usr/bin/env python3
"""Position sizing for trader-budget. Standard library only, no network.

Sizes ONE day-trade pick from trader/plan.json (written by trader-exit) to your
budget and your max loss per trade, and says what it is worth after charges and
slippage. It writes trader/sizing.json with --write. It places nothing and
produces no order: the tool only picks and sizes. trader-risk checks the pick.

    python3 size.py                                  # trader/plan.json + trader/config.json
    python3 size.py --write                          # write trader/sizing.json
    python3 size.py --at 102.50 --write              # size at an entry of Rs 102.50
    python3 size.py --budget 50000 --json            # a different budget, as JSON
    python3 size.py --now "2026-11-23 10:15"         # pin the clock (Indian time)

How many shares. Whole shares only (the NSE cash market trades in units of one):

    shares = the largest count that stays inside every one of
      max loss       the loss if the stop is hit, ALL-IN (the price move to the
                     stop, plus the round-trip charges, plus slippage on both
                     fills), must not exceed max_loss_per_trade_inr
      position cap   budget x max_position_pct / entry
      max order      max_order_inr / entry
      cash           (budget minus the cash buffer) / entry. No leverage is
                     assumed: the position must fit in your own cash.

The all-in max loss is why a small account gets fewer shares than the plain
division suggests: brokerage is a flat amount per order, so the charges are a
bigger part of a small trade. The charge RATES in _common/data/charges.json are
placeholders marked verified: false; put your broker's numbers in
trader/charges.json (or --charges FILE) and the output says when they are checked.

The entry it sizes at: --at PRICE, else the plan's entry, or the price in
trader/prices.json if that is fresh (under 15 minutes old) and higher, because a
price that ran up since the plan means a bigger loss at the same stop. Zero
shares (the stop is too far away for your max loss) is a plain refusal: nothing
is written. So is a reward:risk under min_reward_risk.

Config (trader/config.json, all optional, rupees): budget, cash_buffer_pct (10),
max_position_pct (75), max_order_inr (75000), min_order_inr (1000),
max_loss_per_trade_inr (500), min_reward_risk (1.0), slippage_pct (0.05),
blocked ([]). The defaults are placeholders, not advice: set your own.

Exit codes: 0 = sized, 1 = refused or an error.
Education and tooling, not financial advice.
"""

import argparse
import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import in_costs as costs  # noqa: E402
from _common import in_fmt as fmt  # noqa: E402
from _common import in_instruments as ins  # noqa: E402
from _common import in_market as mk  # noqa: E402

DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_inr": 75000,
    "min_order_inr": 1000,
    "max_loss_per_trade_inr": 500,
    "max_daily_loss_inr": 1500,
    "max_trades_per_day": 3,
    "min_reward_risk": 1.0,
    "max_cost_pct_of_reward": 25,
    "slippage_pct": 0.05,
    "no_entries_before": "09:30",
    "no_entries_after": None,
    "flat_by": None,
    "blocked": [],
    "allow_margin": False,
    "allow_short": False,
    "allow_options": False,
}
EPS = 1e-9
FRESH_MINUTES = 15
SETUP_WORDS = {"orb": "opening-range breakout", "level": "close above a level"}
SAFETY = "Education and tooling, not financial advice."


class SizeError(Exception):
    pass


# ---------------------------------------------------------------- time (Indian time, from _common)

def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as Indian time."""
    return mk.parse_now(text, SizeError)


def parse_utc(text):
    """"2026-11-23T04:45:13Z" -> naive UTC, or None."""
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def utc_iso(now_utc=None):
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return now_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "config"))
    return cfg


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def price_time(doc, sym):
    """When this symbol's price is from: the quote's own time (detail[sym].time), else when it
    was fetched (detail[sym].fetched), else the file's as_of."""
    det = ((doc or {}).get("detail") or {}).get(sym) or {}
    for key in ("time", "fetched"):
        t = parse_utc(det.get(key))
        if t:
            return t
    return parse_utc((doc or {}).get("as_of"))


def fresh_price(doc, sym, now_utc, minutes=FRESH_MINUTES):
    """(price, fetched_utc, fresh) for sym from a prices.json doc, or (None, None, False)."""
    if not isinstance(doc, dict):
        return None, None, False
    p = _f((doc.get("prices") or {}).get(sym))
    if p is None or p <= 0:
        return None, None, False
    t = price_time(doc, sym)
    if t is None:
        return p, None, False
    age = (now_utc - t).total_seconds()
    return p, t, -60 <= age <= minutes * 60


# ---------------------------------------------------------------- money helpers

def tick_round(x, tick):
    return ins.round_to_tick(float(x), tick, "nearest")


def shares_for(rupees, per_share):
    """Whole shares that fit in rupees at per_share, in exact integer paise."""
    u, p = int(round(float(rupees) * 100)), int(round(float(per_share) * 100))
    if p <= 0 or u <= 0:
        return 0
    return u // p


def _price(x):
    return "%.2f" % float(x)


def _num(x):
    """1250 -> "1250", 1118.13 -> "1118.13"."""
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return "%.2f" % x


def _short(x, places=1):
    """6.70 -> "6.7", 12.0 -> "12": a percent without trailing zeros."""
    t = ("%.*f" % (places, float(x))).rstrip("0").rstrip(".")
    return t or "0"


def money(x):
    x = float(x)
    return fmt.inr(x, 0 if abs(x - round(x)) < 0.005 else 2)


def max_shares_for_loss(entry, stop, target, max_loss, slip, charges):
    """The largest share count whose ALL-IN loss at the stop (price move, round-trip charges and
    slippage on both fills) stays within max_loss. The loss only grows with the count, so a
    binary search finds it. The plain division is the upper bound: charges only add to it."""
    risk = entry - stop
    hi = int(max_loss / risk + EPS) if risk > 0 else 0
    if hi <= 0:
        return 0
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        loss = costs.net_outcomes(entry, stop, target, mid, slip, charges)["loss_at_stop"]
        if float(loss) <= max_loss + EPS:
            lo = mid
        else:
            hi = mid - 1
    return lo


# ---------------------------------------------------------------- sizing

def size_trade(plan, cfg, budget=None, at=None, price=None, today=None, charges=None, slippage_pct=None):
    """Pure function: one plan + your rules -> the sizing (a dict). qty may be 0.

    at     an entry price you choose (None = the plan's)
    price  a FRESH latest price; the pick is sized at the higher of it and the plan's entry
    today  Indian date (YYYY-MM-DD); a plan for another day is refused"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    sym = ins.from_yahoo(str(plan.get("symbol") or "").strip())
    if not sym:
        raise SizeError("the plan has no symbol (run trader-exit)")
    side = str(plan.get("side") or "buy").strip().lower()
    if side != "buy":
        raise SizeError("this tool sizes long (buy) day trades only; the plan says %r" % side)
    if today and plan.get("date") and str(plan["date"]) != today:
        raise SizeError("the plan is for %s and today is %s in India: make a new plan with trader-exit"
                        % (plan["date"], today))
    entry_plan, stop, target = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for name, v in (("entry", entry_plan), ("stop", stop), ("target", target)):
        if v is None or v <= 0:
            raise SizeError("the plan needs a positive %s (run trader-exit)" % name)
    blocked = {str(b).upper() for b in (c.get("blocked") or [])}
    if sym in blocked:
        raise SizeError("%s is on your blocked list" % sym)
    tick = _f(plan.get("tick")) or ins.DEFAULT_TICK

    budget = float(budget if budget is not None else (c.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    buf_pct = float(c.get("cash_buffer_pct") or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    slip = float(c.get("slippage_pct") if slippage_pct is None else slippage_pct)
    if not 0 <= slip < 5:
        raise SizeError("slippage_pct is a percent from 0 to under 5")
    charges = charges or costs.load()

    if at is not None:
        ref, ref_from = tick_round(at, tick), "at"
    elif price is not None and tick_round(price, tick) > tick_round(entry_plan, tick):
        ref, ref_from = tick_round(price, tick), "price"
    else:
        ref, ref_from = tick_round(entry_plan, tick), "plan"
    stop, target = tick_round(stop, tick), tick_round(target, tick)
    if stop >= ref:
        raise SizeError("the stop %s is not below the entry %s; a long trade needs its stop underneath"
                        % (_price(stop), _price(ref)))
    if target <= ref:
        raise SizeError("the target %s is not above the entry %s: the move has already happened. "
                        "Skip it or make a new plan with trader-exit" % (_price(target), _price(ref)))
    risk_p = int(round((ref - stop) * 100))
    reward_p = int(round((target - ref) * 100))
    risk = risk_p / 100.0

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    available = max(round(budget - cash_buffer, 2), 0.0)
    cash_note = "budget %s minus the %s cash buffer" % (money(budget), money(cash_buffer))

    limits = []
    max_loss = _f(c.get("max_loss_per_trade_inr"))
    if max_loss:
        q_loss = max_shares_for_loss(ref, stop, target, max_loss, slip, charges)
        limits.append({"name": "max loss", "rupees": max_loss, "per": risk, "shares": q_loss,
                       "how": "%s all-in (move to the stop + charges + %s%% slippage), Rs %s a share before costs"
                              % (money(max_loss), _num(slip), _price(risk))})
    cap_pct = _f(c.get("max_position_pct"))
    if cap_pct:
        cap = round(budget * cap_pct / 100.0, 2)
        limits.append({"name": "position cap", "rupees": cap, "per": ref, "shares": shares_for(cap, ref),
                       "how": "%s%% of %s = %s / %s" % (_num(cap_pct), money(budget), money(cap), _price(ref))})
    max_order = _f(c.get("max_order_inr"))
    if max_order:
        limits.append({"name": "max order", "rupees": max_order, "per": ref, "shares": shares_for(max_order, ref),
                       "how": "%s / %s" % (money(max_order), _price(ref))})
    limits.append({"name": "cash", "rupees": available, "per": ref, "shares": shares_for(available, ref),
                   "how": "%s = %s / %s" % (cash_note, money(available), _price(ref))})

    qty = max(0, min(l["shares"] for l in limits))
    bound = [l["name"] for l in limits if l["shares"] == qty]
    for l in limits:
        l["binds"] = l["name"] == bound[0]
    cost = round(qty * ref, 2)
    price_loss = round(qty * risk_p / 100.0, 2)
    net = costs.net_outcomes(ref, stop, target, qty, slip, charges) if qty > 0 else None
    refusal = None
    if qty == 0:
        if bound[0] == "max loss":
            refusal = ("one share risks Rs %s before costs (entry %s minus stop %s) and the charges and slippage on "
                       "top push even one share past your %s max loss per trade. Nothing sized. Skip this trade, "
                       "or plan a closer stop with trader-exit." % (_price(risk), _price(ref), _price(stop), money(max_loss)))
        else:
            refusal = ("one share of %s costs %s, more than your %s limit (%s). Nothing sized."
                       % (sym, money(ref), bound[0], limits[[l["name"] for l in limits].index(bound[0])]["how"]))
    elif _f(c.get("min_reward_risk")) and reward_p < _f(c["min_reward_risk"]) * risk_p - EPS:
        refusal = ("reward:risk %.2f (target %s, entry %s, stop %s) is under your minimum %s, so trader-risk "
                   "would block it. Nothing sized. Skip it, or make a new plan with trader-exit."
                   % (reward_p / float(risk_p), _price(target), _price(ref), _price(stop), _num(c["min_reward_risk"])))
    elif cost < float(c.get("min_order_inr") or 0) - 0.005:
        refusal = ("%d share%s of %s is %s, under your %s minimum order. Nothing sized."
                   % (qty, "" if qty == 1 else "s", sym, money(cost), money(c["min_order_inr"])))
    cost_block = None
    if net is not None:
        gross_gain = qty * (target - ref)
        net_gain = float(net["gain_at_target"])
        cost_block = {"qty": qty, "slippage_pct": slip, "loss_at_stop": float(net["loss_at_stop"]),
                      "gain_at_target": net_gain,
                      "net_reward_risk": float(net["net_reward_risk"]) if net["net_reward_risk"] is not None else None,
                      "charges_at_stop": float(net["charges_at_stop"]),
                      "charges_at_target": float(net["charges_at_target"]),
                      "cost_share_of_reward_pct": round(100.0 * (gross_gain - net_gain) / gross_gain, 1)
                      if gross_gain > 0 else None,
                      "charges_verified": net["verified"],
                      "charges_file": os.path.basename(net["charges_file"] or "")}
    return {
        "symbol": sym, "side": "buy", "setup": plan.get("setup", ""), "date": plan.get("date"),
        "group": plan.get("group") or "any", "tick": tick,
        "entry_time": plan.get("entry_time", ""), "entry_plan": tick_round(entry_plan, tick),
        "entry": ref, "entry_from": ref_from, "stop": stop, "target": target,
        "risk_per_share": risk, "reward_risk": round(reward_p / float(risk_p), 2),
        "stop_label": plan.get("stop_label", ""), "target_label": plan.get("target_label", ""),
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer, "cash_available": available,
        "max_loss_per_trade_inr": max_loss, "limits": limits, "qty": qty, "bound_by": bound[0],
        "cost": cost, "price_loss_at_stop": price_loss, "costs": cost_block,
        "flat_by": plan.get("flat_by") or c.get("flat_by"), "refusal": refusal,
    }


def sizing_doc(s, now_utc, source=""):
    """trader/sizing.json: what trader-risk reads."""
    return {"created_by": "trader-budget", "schema_version": 2, "source": source, "exchange": "NSE",
            "currency": "INR", "timezone": "Asia/Kolkata", "created_at": utc_iso(now_utc),
            "created_at_ist": mk.to_ist(now_utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": s["symbol"], "group": s["group"], "side": "buy", "setup": s["setup"], "date": s["date"],
            "entry_time": s["entry_time"], "entry": s["entry"], "entry_from": s["entry_from"],
            "entry_plan": s["entry_plan"], "stop": s["stop"], "target": s["target"], "tick": s["tick"],
            "risk_per_share": s["risk_per_share"], "reward_risk": s["reward_risk"], "qty": s["qty"],
            "cost": s["cost"], "price_loss_at_stop": s["price_loss_at_stop"], "bound_by": s["bound_by"],
            "budget": s["budget"], "cash_available": s["cash_available"], "limits": s["limits"],
            "costs": s["costs"], "flat_by": s["flat_by"],
            "max_loss_per_trade_inr": s["max_loss_per_trade_inr"],
            "note": "A size for a pick, not an order. Nothing was placed."}


def report(s, notes=()):
    lines = []
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "setup")
    lines.append("%s buy, %s, entry %s%s" % (s["symbol"], setup, _price(s["entry_plan"]),
                                            (" at " + s["entry_time"]) if s["entry_time"] else ""))
    lines.append("Stop %s%s. Target %s%s." % (_price(s["stop"]), (": " + s["stop_label"]) if s["stop_label"] else "",
                                             _price(s["target"]),
                                             (": " + s["target_label"]) if s["target_label"] else ""))
    for n in notes:
        lines.append(n)
    src = {"plan": "the plan's entry", "price": "the latest price, above the plan's entry",
           "at": "your --at price"}[s["entry_from"]]
    lines.append("Sized at Rs %s (%s). Risk Rs %s a share (entry %s minus stop %s). Reward:risk %.2f before costs."
                 % (_price(s["entry"]), src, _price(s["risk_per_share"]), _price(s["entry"]), _price(s["stop"]),
                    s["reward_risk"]))
    lines.append("")
    lines.append("Limits (whole shares; the smallest one wins):")
    for l in s["limits"]:
        lines.append("  %-13s %-72s %5d shares%s" % (l["name"], l["how"], l["shares"], "  <- binds" if l["binds"] else ""))
    lines.append("")
    if s["refusal"]:
        lines.append("Not sized: " + s["refusal"])
        lines.append(SAFETY)
        return "\n".join(lines)
    c = s["costs"]
    lines.append("Size: %d shares of %s = %s at Rs %s." % (s["qty"], s["symbol"], money(s["cost"]), _price(s["entry"])))
    lines.append("If the stop is hit you lose %s all-in: %s of price move + %s of charges and slippage."
                 % (money(c["loss_at_stop"]), money(s["price_loss_at_stop"]),
                    money(round(c["loss_at_stop"] - s["price_loss_at_stop"], 2))))
    lines.append("If the target is hit you gain %s after charges and slippage (charges %s)."
                 % (money(c["gain_at_target"]), money(c["charges_at_target"])))
    if c["net_reward_risk"] is not None:
        lines.append("Reward:risk left after costs: %.2f (it was %.2f before). Costs and slippage take %s%% of the reward."
                     % (c["net_reward_risk"], s["reward_risk"], _short(c["cost_share_of_reward_pct"] or 0)))
    lines.append("A stop is a plan, not a promise: in a fast market, or at a circuit limit, it can fill below %s or not "
                 "at all." % _price(s["stop"]))
    if not c["charges_verified"]:
        lines.append("The charge rates are placeholders, not checked against any broker's schedule: put yours in "
                     "trader/charges.json.")
    if s["flat_by"]:
        lines.append("Flat by %s IST: anything still open then should be closed." % s["flat_by"])
    lines.append("")
    lines.append("Next: trader-risk checks the pick. Nothing was placed: this tool only sizes.")
    lines.append(SAFETY)
    return "\n".join(lines)


def run(a):
    cfg = load_config(a.config)
    now = parse_now(a.now)
    today = mk.to_ist(now).date().isoformat()
    plan = read_json(a.plan, "plan (run trader-exit first)")
    at = None
    if a.at is not None:
        at = _f(a.at)
        if at is None or at <= 0:
            raise SizeError("--at wants a price, like --at 102.50")
    sym = ins.from_yahoo(str((plan or {}).get("symbol") or ""))
    notes, price = [], None
    ppath = a.prices or ("trader/prices.json" if os.path.exists("trader/prices.json") else None)
    if ppath and at is None and sym:
        pdoc = read_json(ppath, "prices")
        p, t, fresh = fresh_price(pdoc, sym, now)
        entry_plan = _f((plan or {}).get("entry")) or 0
        tick = _f((plan or {}).get("tick")) or ins.DEFAULT_TICK
        if p is not None and fresh:
            price = p
            if tick_round(p, tick) > tick_round(entry_plan, tick):
                notes.append("%s last Rs %s (as of %s IST) is above the plan's entry %s, so the risk is bigger: "
                             "sized at Rs %s." % (sym, _price(p), mk.to_ist(t).strftime("%H:%M"),
                                                  _price(entry_plan), _price(tick_round(p, tick))))
        elif p is not None:
            notes.append("%s in %s is %s: not used (older than %d minutes). Sized at the plan's entry."
                         % (sym, ppath, ("from " + mk.to_ist(t).strftime("%Y-%m-%d %H:%M") + " IST") if t else "undated",
                            FRESH_MINUTES))
    try:
        charges = costs.load(a.charges)
    except costs.CostError as e:
        raise SizeError(str(e))
    s = size_trade(plan, cfg, a.budget, at, price, today, charges, a.slippage_pct)
    wrote = False
    if a.write and not s["refusal"]:
        src = "trader/plan.json (%s %s %s)" % (s["symbol"], s["setup"] or "plan", s["date"] or today)
        d = os.path.dirname(a.write)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.write, "w", encoding="utf-8") as fh:
            json.dump(sizing_doc(s, now, src), fh, indent=2)
            fh.write("\n")
        wrote = True
    if a.json:
        print(json.dumps({"sizing": s, "notes": notes}, indent=2))
    else:
        print(report(s, notes))
        if wrote:
            print("Wrote the sizing to %s" % a.write)
    return 0 if not s["refusal"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Position sizing (trader-budget): one pick, sized to your max loss, "
                                             "after charges and slippage. It places nothing.")
    ap.add_argument("--plan", default="trader/plan.json", help="the plan trader-exit wrote")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--budget", type=float, help="rupees (default: budget in the config)")
    ap.add_argument("--at", metavar="PRICE", help="size at this entry price instead of the plan's")
    ap.add_argument("--prices", help="a prices.json (default trader/prices.json if it exists)")
    ap.add_argument("--charges", metavar="FILE", help="your broker's charges (default trader/charges.json, else the "
                                                      "bundled placeholders)")
    ap.add_argument("--slippage-pct", type=float, help="percent worse than plan on each fill (default: config, 0.05)")
    ap.add_argument("--now", help='pretend it is this Indian time, e.g. "2026-11-23 10:15"')
    ap.add_argument("--write", nargs="?", const="trader/sizing.json", metavar="PATH",
                    help="save the sizing (default trader/sizing.json)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        return run(a)
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
