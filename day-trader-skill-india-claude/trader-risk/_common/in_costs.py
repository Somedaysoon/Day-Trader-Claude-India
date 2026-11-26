"""What a round trip costs in India, for intraday equity. Standard library only.

The rates are data, not code: data/charges.json ships PLACEHOLDER numbers marked
verified: false, and a trader/charges.json in the working folder (same keys)
replaces them. Nothing here knows your broker. Compare the numbers with your
broker's published charges before trusting any net figure, and update them when
the schedule changes (they do).

Per leg: brokerage (the lower of a flat amount and a percent of the order
value), STT on the sell side, exchange transaction charges and SEBI fees on
both sides, stamp duty on the buy side, GST on brokerage + exchange + SEBI.
Each component is rounded half up to the paisa.
"""

import json
import os
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(HERE, "data", "charges.json")
LOCAL = os.path.join("trader", "charges.json")
PAISA = Decimal("0.01")
CRORE = Decimal("10000000")
NEEDED = ("brokerage", "stt_pct_sell", "exchange_txn_pct", "sebi_per_crore", "stamp_pct_buy", "gst_pct")


class CostError(Exception):
    pass


def load(path=None):
    """The charges: an explicit path, else trader/charges.json if it exists, else the bundled placeholders."""
    p = path or (LOCAL if os.path.exists(LOCAL) else BUNDLED)
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as e:
        raise CostError("could not read the charges file %s: %s" % (p, e))
    for k in NEEDED:
        if k not in cfg:
            raise CostError("%s is missing %r" % (p, k))
    b = cfg["brokerage"]
    if not isinstance(b, dict) or "flat" not in b or "pct" not in b:
        raise CostError("%s: brokerage needs flat and pct" % p)
    cfg = dict(cfg)
    cfg["_path"] = p
    return cfg


def _d(x):
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(str(x))


def _r(x):
    return x.quantize(PAISA, rounding=ROUND_HALF_UP)


def brokerage(value, cfg):
    """One executed order: the lower of the flat amount and pct percent of the value."""
    b = cfg["brokerage"]
    return min(_d(b["flat"]), _r(value * _d(b["pct"]) / 100))


def round_trip(buy_price, sell_price, qty, cfg=None):
    """Charges for buying qty at buy_price and selling at sell_price the same day.
    -> dict of Decimals (rupees): brokerage, stt, exchange_txn, sebi, stamp, gst, total, plus
    buy_value, sell_value."""
    cfg = cfg or load()
    if qty <= 0:
        raise CostError("quantity must be above 0")
    bv = _d(buy_price) * qty
    sv = _d(sell_price) * qty
    brok = brokerage(bv, cfg) + brokerage(sv, cfg)
    stt = _r(sv * _d(cfg["stt_pct_sell"]) / 100)
    exch = _r((bv + sv) * _d(cfg["exchange_txn_pct"]) / 100)
    sebi = _r((bv + sv) / CRORE * _d(cfg["sebi_per_crore"]))
    stamp = _r(bv * _d(cfg["stamp_pct_buy"]) / 100)
    gst = _r((brok + exch + sebi) * _d(cfg["gst_pct"]) / 100)
    total = brok + stt + exch + sebi + stamp + gst
    return {"brokerage": brok, "stt": stt, "exchange_txn": exch, "sebi": sebi, "stamp": stamp, "gst": gst,
            "total": total, "buy_value": bv, "sell_value": sv}


def net_outcomes(entry, stop, target, qty, slippage_pct=0, cfg=None):
    """Money at the stop and at the target after charges and slippage, for qty shares.
    Slippage is the percent of price each fill lands worse than the plan, on the entry and on the exit.
    -> {loss_at_stop, gain_at_target, net_reward_risk, charges_at_stop, charges_at_target, slippage_pct,
        qty, verified, charges_file} with Decimal rupees (loss positive)."""
    cfg = cfg or load()
    e, st, tg, s = _d(entry), _d(stop), _d(target), _d(slippage_pct) / 100
    e_fill = e * (1 + s)
    stop_fill = st * (1 - s)
    target_fill = tg * (1 - s)
    c_stop = round_trip(e_fill, stop_fill, qty, cfg)
    c_tgt = round_trip(e_fill, target_fill, qty, cfg)
    loss = (e_fill - stop_fill) * qty + c_stop["total"]
    gain = (target_fill - e_fill) * qty - c_tgt["total"]
    rr = (gain / loss) if loss > 0 else None
    return {"loss_at_stop": _r(loss), "gain_at_target": _r(gain),
            "net_reward_risk": rr.quantize(PAISA, rounding=ROUND_HALF_UP) if rr is not None else None,
            "charges_at_stop": _r(c_stop["total"]), "charges_at_target": _r(c_tgt["total"]),
            "slippage_pct": float(slippage_pct), "qty": qty, "verified": bool(cfg.get("verified")),
            "charges_file": cfg.get("_path")}
