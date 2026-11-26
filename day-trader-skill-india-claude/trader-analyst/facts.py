#!/usr/bin/env python3
"""Indian listed-company earnings analyst.

Consumes official NSE/BSE XBRL instance files downloaded from exchange filing pages.
No undocumented exchange API, authentication bypass, or third-party data dependency.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal, InvalidOperation

NSE_FILINGS = "https://www.nseindia.com/companies-listing/corporate-integrated-filing"
NSE_RESULTS = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
BSE_RESULTS = "https://www.bseindia.com/corporates/Comp_Resultsnew.aspx"

# XBRL local-name aliases. The original tag is always retained in output.
ALIASES = {
    "revenue": [
        "RevenueFromOperations", "RevenueFromOperationsOfContinuingOperations",
        "RevenueFromOperationsIncludingOtherOperatingIncome", "Revenue",
        "RevenueFromSaleOfProducts", "RevenueFromSaleOfProductsAndServices",
        "SaleOfProducts", "SaleOfServices", "IncomeFromOperations"
    ],
    "other_income": ["OtherIncome", "OtherNonOperatingIncome"],
    "total_income": ["TotalIncome", "Income"],
    "cost_of_revenue": [
        "CostOfMaterialsConsumed", "PurchasesOfStockInTrade",
        "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade",
        "CostOfGoodsSold", "CostOfServices"
    ],
    "employee_benefits": ["EmployeeBenefitsExpense"],
    "finance_costs": ["FinanceCosts", "FinanceCost"],
    "depreciation": ["DepreciationDepletionAndAmortisationExpense",
                     "DepreciationAndAmortisationExpense", "DepreciationExpense"],
    "other_expenses": ["OtherExpenses"],
    "total_expenses": ["TotalExpenses", "TotalExpenditure"],
    "operating_profit": ["OperatingProfit", "ProfitBeforeFinanceCostsAndTax"],
    "profit_before_tax": ["ProfitBeforeTax", "ProfitLossBeforeTax"],
    "net_income": ["ProfitLossForPeriod", "ProfitLoss", "ProfitAfterTax",
                   "ProfitForThePeriod", "ProfitLossFromContinuingOperations"],
    "eps_basic": ["BasicEarningsLossPerShareFromContinuingOperations",
                  "BasicEarningsLossPerShare", "EarningsPerShareBasic"],
    "eps_diluted": ["DilutedEarningsLossPerShareFromContinuingOperations",
                    "DilutedEarningsLossPerShare", "EarningsPerShareDiluted"],
}

FLOW_METRICS = {
    "revenue", "other_income", "total_income", "cost_of_revenue",
    "employee_benefits", "finance_costs", "depreciation", "other_expenses",
    "total_expenses", "operating_profit", "profit_before_tax", "net_income"
}
EPS_METRICS = {"eps_basic", "eps_diluted"}

def local_name(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]

def as_date(value):
    return dt.date.fromisoformat(value[:10])

def parse_xbrl_bytes(data, source_name):
    """Extract numeric facts and their contexts from an XBRL instance."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("%s is not a readable XBRL XML file: %s" % (source_name, exc))
    contexts = {}
    for node in root.iter():
        if local_name(node.tag) != "context":
            continue
        cid = node.attrib.get("id")
        if not cid:
            continue
        period = next((x for x in node if local_name(x.tag) == "period"), None)
        if period is None:
            continue
        start = end = instant = None
        for p in period:
            n = local_name(p.tag)
            if n == "startDate": start = (p.text or "")[:10]
            elif n == "endDate": end = (p.text or "")[:10]
            elif n == "instant": instant = (p.text or "")[:10]
        contexts[cid] = {"start": start, "end": end or instant, "instant": instant}
    facts = []
    for node in root.iter():
        context_id = node.attrib.get("contextRef")
        if not context_id or context_id not in contexts:
            continue
        # Exclude text blocks and non-numeric facts.
        raw = (node.text or "").strip().replace(",", "")
        if not raw or raw == "-":
            continue
        raw = raw.replace("(", "-").replace(")", "")
        raw = re.sub(r"\s+", "", raw)
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        unit = node.attrib.get("unitRef")
        facts.append({
            "concept": local_name(node.tag),
            "context": contexts[context_id],
            "value": float(value),
            "unit": unit,
            "decimals": node.attrib.get("decimals"),
        })
    if not facts:
        raise ValueError("%s contained no numeric XBRL facts with contexts" % source_name)
    return facts

def read_xbrl_file(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            candidates = [n for n in z.namelist()
                          if n.lower().endswith((".xml", ".xbrl")) and not n.endswith("/")]
            if not candidates:
                raise ValueError("%s contains no XML/XBRL instance" % path)
            # Prefer the largest XML; taxonomy/schema files are normally smaller.
            name = max(candidates, key=lambda n: z.getinfo(n).file_size)
            return parse_xbrl_bytes(z.read(name), path + "!" + name)
    with open(path, "rb") as f:
        return parse_xbrl_bytes(f.read(), path)

def normalize_concept(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())

ALIAS_LOOKUP = {
    normalize_concept(alias): metric
    for metric, aliases in ALIASES.items() for alias in aliases
}

def collect_report_facts(facts):
    """Return metric -> list of duration facts; select known aliases only."""
    out = defaultdict(list)
    for fact in facts:
        metric = ALIAS_LOOKUP.get(normalize_concept(fact["concept"]))
        if not metric:
            continue
        c = fact["context"]
        if not c.get("end"):
            continue
        if c.get("start"):
            days = (as_date(c["end"]) - as_date(c["start"])).days
            if days < 1 or days > 400:
                continue
        else:
            continue
        out[metric].append({
            "start": c["start"], "end": c["end"], "value": fact["value"],
            "concept": fact["concept"], "unit": fact["unit"]
        })
    return out

def choose_fact(items, start, end):
    matches = [x for x in items if x["start"] == start and x["end"] == end]
    if not matches:
        return None
    # Prefer the first listed alias order only as a deterministic tie-break.
    return matches[0]

def report_to_periods(report, fiscal_year_start=None):
    """Build reported 3-month, YTD and annual observations from one report."""
    output = {}
    for metric, items in report.items():
        for item in items:
            days = (as_date(item["end"]) - as_date(item["start"])).days
            if 80 <= days <= 100:
                key = (metric, item["end"], "quarter")
            elif 150 <= days <= 220:
                key = (metric, item["end"], "half_year")
            elif 260 <= days <= 290:
                key = (metric, item["end"], "nine_month")
            elif 350 <= days <= 380:
                key = (metric, item["end"], "annual")
            else:
                continue
            output[key] = item
    return output

def pct(current, prior):
    if current is None or prior in (None, 0):
        return None
    return round(100.0 * (current - prior) / abs(prior), 1)

def margin(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return round(100.0 * numerator / denominator, 1)

def load_manifest(path):
    with open(path, encoding="utf-8") as f:
        obj = json.load(f)
    rows = obj if isinstance(obj, list) else obj.get("filings")
    if not isinstance(rows, list):
        raise ValueError("Manifest must contain a 'filings' array")
    return rows

def ingest(manifest, quarters=8):
    """Ingest files named in manifest. Latest filing per period/metric wins."""
    # key: canonical company_id (ISIN when available), basis, period_end, metric, period-kind
    chosen = {}
    company_meta = {}
    errors = []
    for item in manifest:
        required = ("file", "basis", "period_end")
        missing = [k for k in required if not item.get(k)]
        if missing:
            errors.append("manifest row missing %s: %s" % (", ".join(missing), item))
            continue
        nse_symbol = (item.get("ticker") or item.get("nse_symbol") or "").strip().upper()
        bse_code = str(item.get("bse_scrip_code") or "").strip()
        isin = (item.get("isin") or "").strip().upper()
        if not (nse_symbol or bse_code or isin):
            errors.append("manifest row needs at least one of ticker/nse_symbol, bse_scrip_code or isin: %s" % item)
            continue
        # ISIN is the canonical identity where available, so NSE and BSE filings
        # for the same issuer are consolidated under one company key.
        company_id = isin or ("NSE:" + nse_symbol if nse_symbol else "BSE:" + bse_code)
        display_ticker = nse_symbol or ("BSE:" + bse_code if bse_code else isin)
        basis = item["basis"].lower()
        if basis not in ("standalone", "consolidated"):
            errors.append("%s: basis must be standalone or consolidated" % item["file"])
            continue
        path = item["file"]
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        try:
            facts = collect_report_facts(read_xbrl_file(path))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            continue
        prior_meta = company_meta.get((company_id, basis), {})
        preferred_nse = nse_symbol or prior_meta.get("nse_symbol")
        preferred_bse = bse_code or prior_meta.get("bse_scrip_code")
        company_meta[(company_id, basis)] = {
            "ticker": preferred_nse or ("BSE:" + preferred_bse if preferred_bse else display_ticker),
            "nse_symbol": preferred_nse, "bse_scrip_code": preferred_bse,
            "name": item.get("company_name") or prior_meta.get("name"),
            "isin": isin or prior_meta.get("isin"),
            "exchange": sorted(set((item.get("exchange") or []) + prior_meta.get("exchange", []))),
            "source": item.get("source", prior_meta.get("source", "NSE/BSE XBRL")),
        }
        filed = item.get("filed", "")
        period_end = item["period_end"][:10]
        for metric, observations in facts.items():
            for obs in observations:
                # Only retain facts ending at this filing's period or its comparable
                # annual/YTD contexts. Prior-period comparatives are useful only if
                # explicitly submitted in a separate manifest filing.
                if obs["end"] != period_end:
                    continue
                days = (as_date(obs["end"]) - as_date(obs["start"])).days
                kind = ("quarter" if 80 <= days <= 100 else
                        "half_year" if 150 <= days <= 220 else
                        "nine_month" if 260 <= days <= 290 else
                        "annual" if 350 <= days <= 380 else None)
                if not kind:
                    continue
                key = (company_id, basis, period_end, metric, kind)
                stamp = (filed, item.get("revision", "original") == "revised")
                if key not in chosen or stamp >= chosen[key]["stamp"]:
                    chosen[key] = {"obs": obs, "stamp": stamp, "item": item}
    # Build a period series for each company/basis/metric, converting cumulative
    # YTD observations into discrete quarters where a prior cumulative is present.
    grouped = defaultdict(dict)
    for (company_id, basis, end, metric, kind), rec in chosen.items():
        grouped[(company_id, basis, metric)][(end, kind)] = rec
    series = defaultdict(dict)
    for group, records in grouped.items():
        company_id, basis, metric = group
        annual_by_year = {}
        for (end, kind), rec in records.items():
            if kind == "annual":
                annual_by_year[end[:4]] = rec["obs"]["value"]
            if kind == "quarter":
                series[group][end] = {
                    "value": rec["obs"]["value"], "derived": False,
                    "start": rec["obs"]["start"], "concept": rec["obs"]["concept"],
                    "source_file": rec["item"]["file"]
                }
        # Derive quarter from YTD: Q2 = H1 - Q1; Q3 = 9M - H1.
        ytd_by_year = defaultdict(dict)
        for (end, kind), rec in records.items():
            if kind in ("half_year", "nine_month"):
                ytd_by_year[end[:4]][kind] = (end, rec)
        for year, ytd in ytd_by_year.items():
            if metric in EPS_METRICS:
                continue  # EPS is not additive across cumulative periods.
            half = ytd.get("half_year")
            nine = ytd.get("nine_month")
            if half:
                h_end, h_rec = half
                q1 = next((v for e, v in series[group].items()
                           if e[:4] == year and e.endswith("-06-30")), None)
                if q1 and h_end not in series[group]:
                    series[group][h_end] = {
                        "value": h_rec["obs"]["value"] - q1["value"],
                        "derived": True, "start": None,
                        "concept": h_rec["obs"]["concept"], "source_file": h_rec["item"]["file"]
                    }
            if nine:
                n_end, n_rec = nine
                h = next((v for e, v in series[group].items()
                          if e[:4] == year and e.endswith("-09-30")), None)
                if h and n_end not in series[group]:
                    # The H1 cumulative amount is reconstructed from Q1 + Q2.
                    q1 = next((v for e, v in series[group].items()
                               if e[:4] == year and e.endswith("-06-30")), None)
                    h1 = (q1["value"] + h["value"]) if q1 else None
                    if h1 is not None:
                        series[group][n_end] = {
                            "value": n_rec["obs"]["value"] - h1,
                            "derived": True, "start": None,
                            "concept": n_rec["obs"]["concept"], "source_file": n_rec["item"]["file"]
                        }
        # Derive Q4 from annual minus Q1-Q3, when all three are available.
        for year, annual in annual_by_year.items():
            if metric in EPS_METRICS:
                continue  # Q4 EPS cannot safely be derived by subtraction.
            # An annual period ending 31 March YYYY contains Q1-Q3 ending
            # June/September/December of the preceding calendar year.
            fiscal_qyear = str(int(year) - 1)
            q1 = series[group].get(fiscal_qyear + "-06-30")
            q2 = series[group].get(fiscal_qyear + "-09-30")
            q3 = series[group].get(fiscal_qyear + "-12-31")
            if q1 and q2 and q3:
                fy_end = "%s-03-31" % year
                if fy_end not in series[group]:
                    series[group][fy_end] = {
                        "value": annual - q1["value"] - q2["value"] - q3["value"],
                        "derived": True, "start": None, "concept": "DerivedFromAnnualMinusQ1Q3",
                        "source_file": None
                    }
    # Render company/basis reports using available normalized metrics.
    output = []
    companies = sorted({(company_id, b) for company_id, b, _m in series})
    for company_id, basis in companies:
        ends = sorted({e for (cid, b, _m), vals in series.items()
                       if cid == company_id and b == basis for e in vals}, reverse=True)[:quarters]
        rows = []
        for end in ends:
            def val(metric):
                return series.get((company_id, basis, metric), {}).get(end, {}).get("value")
            revenue = val("revenue")
            income = val("net_income")
            op = val("operating_profit")
            expenses = val("total_expenses")
            gross = (revenue - val("cost_of_revenue")
                     if revenue is not None and val("cost_of_revenue") is not None else None)
            rows.append({
                "period_end": end, "revenue": revenue, "other_income": val("other_income"),
                "total_income": val("total_income"), "total_expenses": expenses,
                "operating_profit": op, "profit_before_tax": val("profit_before_tax"),
                "net_income": income, "eps_basic": val("eps_basic"),
                "eps_diluted": val("eps_diluted"), "gross_profit": gross,
                "gross_margin": margin(gross, revenue),
                "operating_margin": margin(op, revenue),
                "net_margin": margin(income, revenue),
                "revenue_derived": bool(series.get((company_id,basis,"revenue"),{}).get(end,{}).get("derived")),
                "net_income_derived": bool(series.get((company_id,basis,"net_income"),{}).get(end,{}).get("derived")),
            })
        for idx, row in enumerate(rows):
            prior = next((x for x in rows if x["period_end"][:4] == str(int(row["period_end"][:4])-1)
                          and x["period_end"][5:] == row["period_end"][5:]), None)
            row["revenue_yoy"] = pct(row["revenue"], prior["revenue"] if prior else None)
            row["net_income_yoy"] = pct(row["net_income"], prior["net_income"] if prior else None)
            row["eps_yoy"] = pct(row["eps_diluted"], prior["eps_diluted"] if prior else None)
        meta = company_meta.get((company_id, basis), {})
        output.append({
            "ticker": meta.get("ticker") or company_id, "company_id": company_id,
            "nse_symbol": meta.get("nse_symbol"), "bse_scrip_code": meta.get("bse_scrip_code"),
            "name": meta.get("name"), "isin": meta.get("isin"),
            "exchange": meta.get("exchange", []), "basis": basis,
            "period_end": rows[0]["period_end"] if rows else None, "rows": rows,
            "source": meta.get("source", "NSE/BSE XBRL"),
        })
    return output, errors

def print_report(report):
    print("\n%s | %s | %s | %s" % (
        report.get("name") or report["ticker"], report["ticker"],
        report["basis"].upper(), report.get("isin") or "ISIN not supplied"))
    print("Period       Revenue       PAT        EPS(dil)   Rev YoY   PAT YoY")
    for r in report["rows"]:
        def money(x):
            return "-" if x is None else "{:,.2f}".format(x)
        print("%-12s %12s %12s %10s %8s %8s%s" % (
            r["period_end"], money(r["revenue"]), money(r["net_income"]),
            money(r["eps_diluted"]), "-" if r["revenue_yoy"] is None else "%+.1f%%" % r["revenue_yoy"],
            "-" if r["net_income_yoy"] is None else "%+.1f%%" % r["net_income_yoy"],
            "  [derived]" if r["revenue_derived"] or r["net_income_derived"] else ""))

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True, help="JSON manifest of downloaded official exchange XBRL files")
    ap.add_argument("--quarters", type=int, default=8, help="history to show (default: 8)")
    ap.add_argument("--ticker", action="append", help="filter by NSE symbol, BSE scrip code or ISIN; repeatable")
    ap.add_argument("--basis", choices=("standalone", "consolidated", "both"), default="both")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--metrics", help="write/merge trader/metrics.json")
    args = ap.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        reports, errors = ingest(manifest, args.quarters)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("trader-analyst: %s" % exc, file=sys.stderr)
        return 2
    if args.ticker:
        wanted = {x.upper() for x in args.ticker}
        reports = [r for r in reports if wanted.intersection({
            str(r.get("ticker") or "").upper(), str(r.get("nse_symbol") or "").upper(),
            str(r.get("bse_scrip_code") or "").upper(), str(r.get("isin") or "").upper(),
            str(r.get("company_id") or "").upper()
        })]
    if args.basis != "both":
        reports = [r for r in reports if r["basis"] == args.basis]
    if args.json:
        print(json.dumps({"reports": reports, "errors": errors}, indent=2))
    else:
        for r in reports:
            print_report(r)
        for e in errors:
            print("WARNING: " + e, file=sys.stderr)
    if args.metrics and reports:
        existing = {}
        if os.path.isfile(args.metrics):
            with open(args.metrics, encoding="utf-8") as f:
                existing = {(x.get("ticker"), x.get("basis")): x
                            for x in json.load(f).get("companies", [])}
        for r in reports:
            existing[(r["ticker"], r["basis"])] = {
                "ticker": r["ticker"], "name": r["name"], "isin": r["isin"],
                "exchange": r["exchange"], "basis": r["basis"],
                "period_end": r["period_end"], "rows": r["rows"], "source": r["source"]
            }
        os.makedirs(os.path.dirname(args.metrics) or ".", exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as f:
            json.dump({"as_of": dt.date.today().isoformat(),
                       "companies": list(existing.values())}, f, indent=2)
            f.write("\n")
    return 1 if errors and not reports else 0 if reports else 2

if __name__ == "__main__":
    sys.exit(main())
