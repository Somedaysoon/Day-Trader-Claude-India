---
name: trader-analyst
description: >-
  The Indian Earnings Analyst. Analyses NSE/BSE listed companies using filed
  Indian financial results in XBRL, with standalone and consolidated views,
  eight-quarter history, year-on-year comparisons, and TTM context where
  enough quarters are available. Use before trading around results or when
  asked to assess a company's latest earnings.
argument-hint: "[NSE symbols]"
---

# trader-analyst

This skill analyses reported financial results for companies listed on NSE
and/or BSE. It prioritises exchange-filed financial statements over media
summaries. It does not place orders or make buy/sell recommendations.

## Coverage and source policy

Coverage: **all NSE and BSE equity-listed companies**, including companies
listed on only one exchange. Use ISIN as the canonical company identity;
retain NSE symbol and BSE scrip code as exchange-specific identifiers.

Use official sources only:

1. NSE Integrated Filing – Financials and NSE Financial Results.
2. BSE Corporate Financial Results / Listing Centre filings.
3. Company investor-relations websites for annual reports, presentations,
   earnings releases and call transcripts.

Official entry points:

- NSE Integrated Filing – Financials:
  https://www.nseindia.com/companies-listing/corporate-integrated-filing
- NSE Financial Results:
  https://www.nseindia.com/companies-listing/corporate-filings-financial-results
- BSE Financial Results:
  https://www.bseindia.com/corporates/Comp_Resultsnew.aspx

NSE/BSE web pages provide filing records and downloadable attachments. Do not
assume undocumented JSON endpoints are stable public APIs. Do not bypass
bot protection, scrape behind authentication, or reuse browser cookies. If
the source cannot be fetched reliably, download the XBRL attachment through
the official exchange interface and place it in the local filing store.

## What the tool does

`facts.py` reads official XBRL instance files (XML or ZIP containing XML),
normalises selected Indian financial statement concepts, and produces a
quarterly comparison. It uses the standard library only. Manifest rows can
identify a company by NSE `ticker`/`nse_symbol`, BSE `bse_scrip_code`, or ISIN;
ISIN is preferred as the canonical identity when supplied.

Defaults:
- Eight quarters of history.
- Both standalone and consolidated reports.
- NSE/BSE/company sources only.
- Original filing retained unless a later revised filing is supplied.

Run:

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" --help
```

## Filing workflow

1. Identify the company using ISIN, NSE symbol and BSE scrip code. Never
   assume that an NSE symbol exists for a BSE-only company.
2. From NSE/BSE, obtain the relevant Integrated Filing – Financial XBRL
   attachment. Obtain both standalone and consolidated versions when filed.
   For BSE-only issuers, omit `ticker` and supply `bse_scrip_code`; ISIN is the
   canonical company identity when available.
3. Save the original exchange attachment without editing it. Record its
   exchange, filing date, period end, original/revised status and source URL.
4. Add one row per XBRL attachment to a JSON manifest. Example:

```json
{
  "filings": [
    {
      "file": "filings/RELIANCE_2026-09-30_consolidated.zip",
      "ticker": "RELIANCE",
      "bse_scrip_code": "500325",
      "company_name": "Reliance Industries Limited",
      "isin": "INE002A01018",
      "exchange": ["NSE", "BSE"],
      "basis": "consolidated",
      "period_end": "2026-09-30",
      "filed": "2026-11-14",
      "revision": "original",
      "source": "NSE Integrated Filing",
      "source_url": "https://www.nseindia.com/companies-listing/corporate-integrated-filing"
    },
    {
      "file": "filings/RELIANCE_2026-09-30_standalone.zip",
      "ticker": "RELIANCE",
      "company_name": "Reliance Industries Limited",
      "isin": "INE002A01018",
      "exchange": ["NSE", "BSE"],
      "basis": "standalone",
      "period_end": "2026-09-30",
      "filed": "2026-11-14",
      "revision": "original",
      "source": "NSE Integrated Filing",
      "source_url": "https://www.nseindia.com/companies-listing/corporate-integrated-filing"
    }
  ]
}
```

5. Run:

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" --manifest trader/india-filings.json
python3 "${CLAUDE_SKILL_DIR}/facts.py" --manifest trader/india-filings.json --ticker RELIANCE --basis consolidated
python3 "${CLAUDE_SKILL_DIR}/facts.py" --manifest trader/india-filings.json --json
python3 "${CLAUDE_SKILL_DIR}/facts.py" --manifest trader/india-filings.json --metrics trader/metrics.json
```

## Normalisation and interpretation rules

- Preserve the original XBRL concept, unit and source file. Normalised values
  are a convenience layer, not a replacement for the filed statement.
- Never combine standalone and consolidated numbers.
- Treat a missing metric as unavailable, not zero.
- Prefer direct three-month facts. Where a filing provides only cumulative
  values, derive a quarter only when the preceding cumulative period is
  available and the periods align.
- Derive Q4 only when the annual amount and all three preceding quarters are
  available on the same basis. Mark derived values.
- Eight quarters means eight reported quarter-end observations, not eight
  arbitrary XBRL contexts.
- Compare with the corresponding quarter of the previous year. Do not
  interpret quarter-on-quarter growth without considering seasonality.
- Do not assume all companies use identical fiscal calendars. Validate the
  period end and fiscal year before deriving Q4.
- Do not apply manufacturing gross-margin logic to banks, NBFCs, insurers,
  or other financial companies. Their income and expense structures differ.
- EPS is per share and must not be summed across periods unless the reporting
  basis and periods make a TTM calculation valid.
- Revised filings supersede original filings only when the revision metadata
  and period match. Retain the earlier file for auditability.
- The current parser supports common XBRL concept aliases. If a company's
  taxonomy uses a different tag, do not guess: inspect the XBRL and add a
  reviewed alias mapping.

## Metrics and limitations

The normalised output currently covers revenue from operations, other income,
total income, expenses, operating profit (when explicitly tagged), profit
before tax, profit after tax, basic/diluted EPS, and selected cost components.
Gross profit is calculated only when both revenue and cost of revenue are
available and comparable.

The tool does not yet provide a complete Indian taxonomy mapping for every
industry. Banking, NBFC, insurance, REIT/InvIT and unusual corporate structures
need sector-specific mappings and validation. The parser reports only concepts
it recognises; absence is not proof that the company did not report the item.

The current tool reads local official XBRL downloads; it does not autonomously
download all exchange filings. This is deliberate until a documented, stable
and permitted public retrieval interface is available. Filing discovery and
download should be handled through official exchange pages or a future
approved adapter.

## Earnings releases and calls

Read the exchange-filed PDF result and the company's official earnings
presentation or transcript for context, guidance, segment commentary and
management explanations. Keep reported facts separate from management claims.
Record the source link and filing date.

## Output

For each company, report:
- Company, ticker, ISIN and exchange(s).
- Standalone or consolidated basis.
- Latest quarter and eight-quarter trend where available.
- Revenue, PAT, EPS, growth and available margins.
- Whether values are directly reported or derived.
- Filing date, revision status and source.
- Missing data or taxonomy limitations.

## What it never does

- Never calls a stock a buy or sell.
- Never fabricates missing values or silently substitutes standalone for
  consolidated figures.
- Never treats an unverified third-party number as an exchange filing.
- Never submits an order. Real trade execution remains manual and separate.

Education and tooling, not financial advice.
