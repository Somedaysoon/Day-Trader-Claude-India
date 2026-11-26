#!/usr/bin/env python3
"""Indian market disclosure tracker for trader-insider.

Imports official exchange CSV/JSON exports into a local, deduplicated event log.
It deliberately does not scrape undocumented exchange endpoints or bypass
browser controls. Standard library only.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_LOG = Path("trader/state/insider_events.jsonl")
DEFAULT_WATCHLIST = Path("trader/insider_watchlist.json")
DEFAULT_WATCH = [
    {"name": "Vijay Kedia", "aliases": []},
    {"name": "Rakesh Jhunjhunwala", "aliases": [], "historical_only": True},
    {"name": "Rekha Jhunjhunwala", "aliases": []},
    {"name": "Dolly Khanna", "aliases": []},
    {"name": "Ashish Kacholia", "aliases": []},
    {"name": "Mukul Agrawal", "aliases": []},
]

ALIASES = {
    "symbol": ["symbol", "ticker", "scrip", "scrip code", "security symbol", "nse symbol"],
    "isin": ["isin", "isin number", "security isin"],
    "company": ["company", "company name", "security name", "name of company"],
    "person": ["person", "name of person", "acquirer", "seller", "client name", "investor", "shareholder name", "name of shareholder"],
    "category": ["category", "category of person", "relationship", "person category"],
    "action": ["action", "type", "transaction type", "buy/sell", "buy or sell", "nature of transaction"],
    "quantity": ["quantity", "quantity traded", "buy quantity", "sell quantity", "no. of shares", "number of shares", "securities acquired/disposed", "shares"],
    "price": ["price", "trade price", "traded price", "average price"],
    "value": ["value", "transaction value", "value of securities", "amount", "consideration"],
    "holding_shares": ["holding shares", "shares held post", "post holding", "securities held post acquisition/disposal", "shareholding shares", "shares held", "number of equity shares held", "no of fully paid up equity shares held"],
    "holding_pct": ["holding pct", "holding %", "% holding", "shareholding percentage", "percentage", "percentage of shareholding", "% of shareholding", "shareholding as a % of total no of shares"],
    "trade_date": ["trade date", "date of transaction", "date of acquisition", "date of acquisition of shares/sale of shares", "date of allotment/acquisition/sale", "date"],
    "as_on_date": ["as on date", "quarter end", "period ended", "reporting date"],
    "filing_date": ["filing date", "submission date", "broadcast date", "date of filing", "dissemination date"],
    "exchange": ["exchange", "stock exchange"],
    "regulation": ["regulation", "regulation number", "disclosure under"],
    "source_url": ["source url", "url", "link", "document link", "filing link"],
    "remarks": ["remarks", "revision remarks", "description", "details"],
}

KIND_ALIASES = {
    "insider": "insider_transaction", "pit": "insider_transaction",
    "insider_transaction": "insider_transaction", "regulation 7(2)": "insider_transaction",
    "sast": "sast_disclosure", "sast_disclosure": "sast_disclosure",
    "bulk": "bulk_deal", "bulk_deal": "bulk_deal",
    "block": "block_deal", "block_deal": "block_deal",
    "shareholding": "shareholding_snapshot", "shareholding_pattern": "shareholding_snapshot",
    "shareholding_snapshot": "shareholding_snapshot", "super_investor": "shareholding_snapshot",
}


def clean_key(s):
    return re.sub(r"[^a-z0-9%]+", " ", str(s or "").strip().lower()).strip()


def pick(row, field):
    lookup = {clean_key(k): v for k, v in row.items()}
    for alias in ALIASES[field]:
        key = clean_key(alias)
        if key in lookup and str(lookup[key] or "").strip():
            return str(lookup[key]).strip()
    return ""


def number(value):
    if value is None: return None
    s = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "")
    s = s.replace("%", "").strip()
    if not s or s in {"-", "-", "NA", "N/A"}: return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def norm_date(value):
    s = str(value or "").strip()
    if not s: return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d/%m/%y"):
        try: return datetime.strptime(s[:11], fmt).date().isoformat()
        except ValueError: pass
    try: return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError: return None


def read_source(path):
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            for k in ("data", "results", "records", "rows"):
                if isinstance(data.get(k), list): return data[k]
            return [data]
        if isinstance(data, list): return data
        raise ValueError("JSON must contain an array of records or an object with data/results/records/rows")
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def normalize(row, kind, exchange_default=None):
    kind_key = clean_key(kind)
    kind_lookup = {clean_key(k): v for k, v in KIND_ALIASES.items()}
    kind = kind_lookup.get(kind_key, kind_key.replace(" ", "_"))
    person = pick(row, "person")
    event = {
        "kind": kind,
        "person": person or None,
        "category": pick(row, "category") or None,
        "company": pick(row, "company") or None,
        "symbol": pick(row, "symbol").upper() or None,
        "isin": pick(row, "isin").upper() or None,
        "exchange": pick(row, "exchange") or exchange_default,
        "action": pick(row, "action") or None,
        "quantity": number(pick(row, "quantity")),
        "price": number(pick(row, "price")),
        "value": number(pick(row, "value")),
        "holding_shares": number(pick(row, "holding_shares")),
        "holding_pct": number(pick(row, "holding_pct")),
        "trade_date": norm_date(pick(row, "trade_date")),
        "as_on_date": norm_date(pick(row, "as_on_date")),
        "filing_date": norm_date(pick(row, "filing_date")),
        "regulation": pick(row, "regulation") or None,
        "source_url": pick(row, "source_url") or None,
        "remarks": pick(row, "remarks") or None,
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if kind == "shareholding_snapshot":
        if event["holding_shares"] is None:
            event["holding_shares"] = event["quantity"]
        event["quantity"] = None
        if not event["as_on_date"]:
            event["as_on_date"] = event["trade_date"]
            event["trade_date"] = None
    # Use stable business fields for deduplication; imported_at and URL revisions
    # must not create duplicate transactions.
    key_fields = {k: event.get(k) for k in (
        "kind", "person", "company", "symbol", "isin", "action", "quantity",
        "price", "value", "holding_shares", "holding_pct", "trade_date", "as_on_date", "filing_date")}
    event["event_id"] = hashlib.sha256(json.dumps(key_fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    return event


def load_log(path):
    p = Path(path)
    if not p.exists(): return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try: out.append(json.loads(line))
            except json.JSONDecodeError: continue
    return out


def import_rows(args):
    rows = read_source(args.file)
    events = [normalize(r, args.kind, args.exchange) for r in rows]
    p = Path(args.log); p.parent.mkdir(parents=True, exist_ok=True)
    existing = load_log(p); seen = {e.get("event_id") for e in existing}
    fresh = []
    for event in events:
        if event["event_id"] not in seen:
            fresh.append(event)
            seen.add(event["event_id"])
    with p.open("a", encoding="utf-8") as f:
        for e in fresh: f.write(json.dumps(e, ensure_ascii=False) + "\n")
    no_source = sum(not e.get("source_url") for e in fresh)
    print(f"Imported {len(fresh)} new records; skipped {len(events)-len(fresh)} duplicates.")
    if no_source: print(f"Warning: {no_source} new records have no source URL; verify them before relying on them.")


def load_watchlist(path):
    p = Path(path)
    if not p.exists(): return DEFAULT_WATCH
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("investors", data) if isinstance(data, dict) else data


def watchlist_cmd(args):
    p = Path(args.watchlist); entries = load_watchlist(p)
    if args.add:
        if any(x["name"].casefold() == args.add.casefold() for x in entries):
            print("Already on watchlist."); return
        entries.append({"name": args.add, "aliases": []})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"investors": entries}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for item in entries:
        suffix = " (historical only)" if item.get("historical_only") else ""
        print(f"- {item['name']}{suffix}")


def investor_match(name, watchlist):
    n = (name or "").casefold()
    for item in watchlist:
        candidates = [item["name"]] + item.get("aliases", [])
        if any(n == c.casefold() or c.casefold() in n for c in candidates if c): return item["name"]
    return None


def report(args):
    events = load_log(args.log)
    since = norm_date(args.since) if args.since else None
    watch = load_watchlist(args.watchlist)
    out = []
    for e in events:
        event_date = e.get("filing_date") or e.get("as_on_date") or e.get("trade_date") or ""
        if since and event_date < since: continue
        if args.symbol and (e.get("symbol") or "").upper() != args.symbol.upper(): continue
        if args.person and args.person.casefold() not in (e.get("person") or "").casefold(): continue
        if args.kind and e.get("kind") != args.kind: continue
        e = dict(e)
        e["watchlist_match"] = investor_match(e.get("person"), watch)
        out.append(e)
    out.sort(key=lambda x: (x.get("filing_date") or x.get("as_on_date") or x.get("trade_date") or "", x.get("symbol") or ""), reverse=True)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False)); return
    if not out:
        print("No matching records in the local event log. Import official NSE/BSE CSV or JSON exports first."); return
    for e in out:
        who = e.get("person") or "Name not supplied"
        company = e.get("company") or e.get("symbol") or "Unknown security"
        when = e.get("trade_date") or e.get("as_on_date") or e.get("filing_date") or "Date unavailable"
        action = e.get("action") or e.get("kind")
        parts = [f"{when} | {company} ({e.get('symbol') or 'no symbol'}) | {who} | {action}"]
        if e.get("quantity") is not None: parts.append(f"qty={e['quantity']:g}")
        if e.get("value") is not None: parts.append(f"value=₹{e['value']:,.0f}")
        if e.get("holding_pct") is not None: parts.append(f"holding={e['holding_pct']:g}%")
        if e.get("watchlist_match"): parts.append(f"WATCHLIST: {e['watchlist_match']}")
        if not e.get("source_url"): parts.append("SOURCE URL MISSING - verify")
        print("  ".join(parts))
        if e.get("source_url"): print("  Source:", e["source_url"])


def overlaps(args):
    events = load_log(args.log); watch = load_watchlist(args.watchlist)
    latest = {}
    for e in events:
        if e.get("kind") != "shareholding_snapshot": continue
        investor = investor_match(e.get("person"), watch)
        if not investor or not e.get("symbol"): continue
        d = e.get("as_on_date") or e.get("filing_date") or ""
        key = (investor, e["symbol"])
        if key not in latest or d > latest[key].get("_date", ""):
            latest[key] = dict(e, _date=d, _investor=investor)
    by_symbol = {}
    for e in latest.values(): by_symbol.setdefault(e["symbol"], []).append(e)
    found = {s: rows for s, rows in by_symbol.items() if len({r["_investor"] for r in rows}) >= args.minimum}
    if not found:
        print("No multi-investor overlap found in imported shareholding snapshots."); return
    for symbol, rows in sorted(found.items()):
        print(f"{symbol}: " + ", ".join(f"{r['_investor']} ({r.get('holding_pct')}%)" for r in rows))
        print("  Snapshot overlap only; it is not evidence of same-day buying or coordination.")


def changes(args):
    """Compare the latest two imported snapshots for each tracked investor/security."""
    events = load_log(args.log); watch = load_watchlist(args.watchlist)
    grouped = {}
    for e in events:
        if e.get("kind") != "shareholding_snapshot" or not e.get("symbol"):
            continue
        investor = investor_match(e.get("person"), watch)
        if not investor:
            continue
        snap_date = e.get("as_on_date") or e.get("filing_date") or ""
        if not snap_date:
            continue
        grouped.setdefault((investor, e["symbol"]), {})[snap_date] = e
    found = 0
    for (investor, symbol), snapshots in sorted(grouped.items()):
        dates = sorted(snapshots)
        if len(dates) < 2:
            continue
        old, new = snapshots[dates[-2]], snapshots[dates[-1]]
        old_shares, new_shares = old.get("holding_shares"), new.get("holding_shares")
        old_pct, new_pct = old.get("holding_pct"), new.get("holding_pct")
        if old_shares is None and new_shares is None and old_pct is None and new_pct is None:
            continue
        delta_shares = (new_shares - old_shares) if old_shares is not None and new_shares is not None else None
        delta_pct = (new_pct - old_pct) if old_pct is not None and new_pct is not None else None
        print(f"{investor} | {symbol} | {dates[-2]} -> {dates[-1]}")
        if delta_shares is not None: print(f"  Shares: {old_shares:g} -> {new_shares:g} ({delta_shares:+g})")
        if delta_pct is not None: print(f"  Holding: {old_pct:g}% -> {new_pct:g}% ({delta_pct:+g} percentage points)")
        print("  Snapshot comparison only; no exact transaction date or price can be inferred.")
        found += 1
    if not found:
        print("No comparable two-period watchlist snapshots found. Import at least two dated shareholding exports.")


def build_parser():
    p = argparse.ArgumentParser(description="Indian insider, promoter and super-investor disclosure tracker")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("import", help="import an official exchange CSV/JSON export")
    i.add_argument("--kind", required=True, choices=sorted(KIND_ALIASES))
    i.add_argument("--file", required=True)
    i.add_argument("--exchange", choices=["NSE", "BSE", "NSE+BSE"], default=None)
    i.add_argument("--log", default=str(DEFAULT_LOG)); i.set_defaults(func=import_rows)
    r = sub.add_parser("report", help="filter imported disclosures")
    r.add_argument("--since"); r.add_argument("--symbol"); r.add_argument("--person")
    r.add_argument("--kind", choices=sorted(set(KIND_ALIASES.values())))
    r.add_argument("--json", action="store_true"); r.add_argument("--log", default=str(DEFAULT_LOG))
    r.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST)); r.set_defaults(func=report)
    w = sub.add_parser("watchlist", help="list or add tracked investors")
    w.add_argument("--add"); w.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST)); w.set_defaults(func=watchlist_cmd)
    c = sub.add_parser("changes", help="compare two latest snapshots for tracked investors")
    c.add_argument("--log", default=str(DEFAULT_LOG)); c.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST)); c.set_defaults(func=changes)
    o = sub.add_parser("overlaps", help="find investors appearing in the same latest snapshots")
    o.add_argument("--minimum", type=int, default=2); o.add_argument("--log", default=str(DEFAULT_LOG))
    o.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST)); o.set_defaults(func=overlaps)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    try: args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr); raise SystemExit(2)
