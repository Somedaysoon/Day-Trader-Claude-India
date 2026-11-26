#!/usr/bin/env python3
"""Indian-market news monitor for trader-news. Standard library only.

Reads configured Indian regulator/exchange RSS feeds and optional downloaded BSE
CSV announcements. Adds watchlist relevance, event tags and NSE/BSE price context.
It only reports signals and never places or modifies orders.
"""

import argparse
import csv
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")
UTC = dt.timezone.utc
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
BROWSER_UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
KEEP_DAYS = 30
MOVE_PCT = 3.0
DEFAULT_FEEDS = "trader/news_feeds.json"

KEYWORDS = [
    ("earnings", r"\b(results?|earnings?|profit|loss|revenue|eps|guidance|quarterly)\b"),
    ("analyst", r"\b(upgrade[sd]?|downgrade[sd]?|price target|brokerage|rating)\b"),
    ("legal", r"\b(lawsuit|court|tribunal|investigation|probe|fraud|forensic audit)\b"),
    ("deal", r"\b(acquisition|acquire[sd]?|merger|amalgamation|demerger|takeover|order win|contract)\b"),
    ("capital", r"\b(dividend|buyback|bonus|split|rights issue|preferential issue|qip|fund raising)\b"),
    ("people", r"\b(managing director|director|chief executive|ceo|cfo|resign|appointment|appointed)\b"),
    ("risk", r"\b(default|insolvency|bankruptcy|credit rating|downgrade|pledge|fraud|recall)\b"),
    ("halt", r"\b(trading halt|suspension of trading|suspended from trading|surveillance measure)\b"),
    ("regulatory", r"\b(sebi|rbi|regulat(?:ion|ory)|circular|compliance|penalty|fine|show cause|order)\b"),
    ("macro", r"\b(repo rate|monetary policy|inflation|cpi|wpi|gdp|fiscal|budget|crude oil|rupee|forex|gst|tariff)\b"),
]
IMPORTANT_TAGS = {"earnings", "legal", "deal", "capital", "risk", "halt", "regulatory", "macro"}

class NewsError(Exception):
    pass


def parse_time(value):
    """RSS/Atom date -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_ist(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """Return {symbol: company name}; symbols are normally NSE symbols."""
    out = {}
    wl = load_json(watchlist) or {}
    for hit in wl.get("hits", []):
        sym = hit.get("symbol") or hit.get("ticker")
        if sym:
            out[str(sym).upper()] = hit.get("name") or hit.get("company")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(str(pl["symbol"]).upper(), pl.get("name"))
    pf = load_json(portfolio) or {}
    for pos in pf.get("positions", []):
        sym = pos.get("symbol") or pos.get("ticker")
        if sym:
            out[str(sym).upper()] = pos.get("name") or out.get(str(sym).upper())
    return out


def load_feed_config(path=DEFAULT_FEEDS):
    cfg = load_json(path)
    if cfg is None:
        raise NewsError("feed configuration not found: %s (copy trader-news/news_feeds.example.json to trader/news_feeds.json)" % path)
    feeds = cfg.get("feeds", [])
    if not isinstance(feeds, list):
        raise NewsError("feed config 'feeds' must be a list")
    return feeds


class Fetcher:
    def __init__(self, offline=None, pause=0.2):
        self.offline = offline
        self.pause = pause
        self.last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self.last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self.last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))


def local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child_text(element, names):
    names = {n.lower() for n in names}
    for child in element.iter():
        if local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def parse_feed(data, feed_name, category="market"):
    """Parse RSS 2.0 or Atom into normalized news items."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("feed %s is not valid XML: %s" % (feed_name, exc))
    out = []
    for el in root.iter():
        kind = local_name(el.tag)
        if kind not in {"item", "entry"}:
            continue
        title = child_text(el, {"title"})
        link = child_text(el, {"link"})
        if not link:
            for node in el.iter():
                if local_name(node.tag) == "link":
                    link = node.get("href", "")
                    if node.get("rel", "alternate") in {"alternate", ""}:
                        break
        summary = child_text(el, {"description", "summary", "content", "encoded"})
        date_text = child_text(el, {"pubdate", "published", "updated", "date"})
        when = parse_time(date_text)
        guid = child_text(el, {"guid", "id"}) or link or (title + date_text)
        fp = hashlib.sha1((feed_name + "|" + guid).encode("utf-8")).hexdigest()
        out.append({"id": "feed:" + fp, "source": feed_name, "kind": "announcement" if category in {"exchange", "filings"} else "headline",
                    "category": category, "ticker": "", "title": title, "summary": summary,
                    "link": link, "time": when})
    return out



def parse_bse_time(value):
    """BSE CSV local timestamps are IST when no explicit UTC offset is supplied."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).replace(tzinfo=IST).astimezone(UTC)
        except ValueError:
            pass
    parsed = parse_time(value)
    if parsed is not None and parsed.tzinfo == UTC and not re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", value, re.I):
        try:
            return dt.datetime.fromisoformat(value).replace(tzinfo=IST).astimezone(UTC)
        except ValueError:
            pass
    return parsed


def _norm_header(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").strip().lower())


def parse_bse_csv(path):
    """Import a BSE announcement CSV downloaded manually from BSE.

    Column names vary between BSE downloads; common variants are recognized.
    Original CSV is never modified.
    """
    out = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise NewsError("BSE CSV has no header row")
        for row_no, row in enumerate(reader, 2):
            values = {_norm_header(k): (v or "").strip() for k, v in row.items() if k}
            def get(*keys):
                for key in keys:
                    if values.get(key):
                        return values[key]
                return ""
            company = get("companyname", "company", "securityname", "scripname", "issuername")
            code = get("scripcode", "scripcd", "securitycode", "scrip")
            subject = get("subject", "newssub", "headline", "announcement", "news")
            category = get("category", "subcategory", "announcementtype")
            link = get("attachment", "attachmenturl", "attachmentname", "pdf", "link", "url")
            date_text = get("date", "broadcastdate", "broadcastdatetime", "newsdate", "newsdt", "timestamp")
            when = parse_bse_time(date_text)
            title = " - ".join(x for x in (company, subject) if x) or "BSE announcement row %d" % row_no
            identity = get("id", "announcementid", "newsid") or "%s|%s|%s|%s" % (code, company, subject, date_text)
            fp = hashlib.sha1(("BSE|" + identity).encode("utf-8")).hexdigest()
            out.append({"id": "bse:" + fp, "source": "BSE CSV", "kind": "announcement", "category": category or "exchange",
                        "ticker": code, "title": title, "summary": get("details", "description", "remark", "purpose"),
                        "link": link, "time": when, "bse_scrip_code": code})
    return out


def yahoo_symbol(symbol):
    """Convert a plain NSE symbol to Yahoo's .NS suffix; retain explicit suffix."""
    s = symbol.strip().upper()
    if s.endswith((".NS", ".BO")):
        return s
    if "." in s:
        return s
    return s + ".NS"


def parse_chart(data, ticker):
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": dt.datetime.fromtimestamp(when, UTC) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for ticker in sorted(watch):
        yahoo = yahoo_symbol(ticker)
        try:
            data = fetcher.get(CHART % yahoo, "chart-%s.json" % ticker)
            out.append(parse_chart(data, ticker))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (ticker, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for symbol, name in watch.items():
        alternatives = [r"\b%s\b" % re.escape(symbol)]
        if name and len(name) >= 4:
            alternatives.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[symbol] = re.compile("|".join(alternatives), re.I)
    return pats


def tag(items, watch):
    pats = mention_patterns(watch)
    for item in items:
        hay = "%s %s" % (item.get("title", ""), item.get("summary", ""))
        touches = set()
        for symbol, rx in pats.items():
            if rx.search(hay) or item.get("ticker", "").upper() == symbol:
                touches.add(symbol)
        item["touches"] = sorted(touches)
        item["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        cat = str(item.get("category", "")).lower()
        if cat in {"macro", "regulatory", "policy"} and cat not in item["tags"]:
            item["tags"].append(cat)
        item["important"] = bool(set(item["tags"]) & IMPORTANT_TAGS)
    return items


def dedupe(items):
    seen, out = {}, []
    for item in items:
        keys = [item["id"], item.get("link", ""),
                "%s|%s" % (re.sub(r"\W+", " ", item["title"].lower()).strip(), item["time"].date() if item.get("time") else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            hit["touches"] = sorted(set(hit.get("touches", [])) | set(item.get("touches", [])))
            hit["tags"] = sorted(set(hit.get("tags", [])) | set(item.get("tags", [])))
            continue
        for key in keys:
            if key:
                seen[key] = item
        out.append(item)
    return out


def fingerprint(item):
    return hashlib.sha1(item["id"].encode("utf-8")).hexdigest()[:16]


def collect(fetcher, watch, feeds, bse_csv=None, errors=None):
    errors = [] if errors is None else errors
    items = []
    for index, feed in enumerate(feeds):
        if not feed.get("enabled", True):
            continue
        name = feed.get("name") or "Feed %d" % (index + 1)
        url = (feed.get("url") or "").strip()
        if not url:
            errors.append("%s: feed URL not configured; copy it from the official NSE RSS directory" % name)
            continue
        try:
            data = fetcher.get(url, "feed-%s.xml" % re.sub(r"\W+", "_", name.lower()))
            items.extend(parse_feed(data, name, feed.get("category", "market")))
        except NewsError as exc:
            errors.append("%s: %s" % (name, exc))
    if bse_csv:
        try:
            items.extend(parse_bse_csv(bse_csv))
        except (OSError, NewsError, csv.Error) as exc:
            errors.append("BSE CSV: %s" % exc)
    return tag(items, watch)


def select(items, state, now, hours=48, show_all=False):
    seen = state.setdefault("seen", {})
    cutoff = now - dt.timedelta(hours=hours)
    ahead = now + dt.timedelta(minutes=10)
    out = []
    for item in items:
        when = item.get("time")
        if when is not None and (when < cutoff or when > ahead):
            continue
        # Show explicit ticker matches, and broad regulator/macro items even if
        # they do not name a watched company. Do not discard unknown-date items.
        if not item.get("touches") and not (set(item.get("tags", [])) & {"macro", "regulatory"}):
            continue
        fp = fingerprint(item)
        item["new"] = fp not in seen
        if item["new"] or show_all:
            out.append(item)
    out.sort(key=lambda x: (x.get("time") or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for item in items:
        seen.setdefault(fingerprint(item), stamp)
    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for move in moves:
        flag = "MOVING" if abs(move["pct"]) >= move_pct else ""
        when = " at %s IST" % to_ist(move["time"]).strftime("%H:%M") if move.get("time") else ""
        out.append("%-14s ₹%s %+.2f%% vs previous close ₹%s%s  %s" % (
            move["ticker"], format(move["price"], ",.2f"), move["pct"], format(move["prev_close"], ",.2f"), when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for item in items:
        when = to_ist(item["time"]).strftime("%Y-%m-%d %H:%M IST") if item.get("time") else "time unknown"
        mark = "!" if item.get("important") else " "
        who = ",".join(item.get("touches", [])) or "MARKET"
        out.append("%s %s  %-18s %-16s %s" % (mark, when, who, item["source"], item["title"]))
        extra = []
        if item.get("tags"):
            extra.append("tags: " + ", ".join(item["tags"]))
        if item.get("summary"):
            extra.append(re.sub(r"\s+", " ", item["summary"])[:260])
        extra.append(item.get("link", ""))
        out.append("      " + "  ".join(x for x in extra if x))
    for error in errors:
        out.append("note: " + error)
    out.append("Checked %s. Headlines are leads, not facts; open the original filing before relying on it. "
               "Signals only; no orders are placed. Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def json_ready(items):
    result = []
    for item in items:
        row = dict(item)
        row["time"] = item["time"].isoformat() if item.get("time") else None
        result.append(row)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Indian market news and corporate announcement monitor")
    ap.add_argument("--tickers", nargs="*", help="default: trader watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--feeds", default=DEFAULT_FEEDS, help="configured regulator/exchange RSS feeds JSON")
    ap.add_argument("--bse-csv", help="path to a manually downloaded BSE corporate-announcements CSV")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show already-seen items too")
    ap.add_argument("--no-moves", action="store_true", help="skip price context")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this large (percent)")
    ap.add_argument("--offline", metavar="DIR", help="read feed-*.xml and chart-*.json from DIR")
    ap.add_argument("--now", help="ISO time for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update state")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch = {t.upper(): known.get(t.upper()) for t in args.tickers}
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not watch:
        print("news: no tickers. Pass --tickers or run trader-scanner first (it writes trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else dt.datetime.now(UTC)
    if now is None:
        print("news: --now must be a valid ISO date/time", file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    errors = []
    try:
        feeds = load_feed_config(args.feeds)
    except (NewsError, ValueError) as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, feeds, args.bse_csv, errors)
    items = dedupe(items)
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": json_ready(shown), "notes": errors}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return 0 if any(item.get("new") for item in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
