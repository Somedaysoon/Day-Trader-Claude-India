---
name: trader-insider
description: >-
  Tracks public Indian-market disclosures by company insiders, promoters,
  substantial shareholders, bulk/block deal participants and a configurable
  watchlist of major investors. Imports NSE/BSE disclosure exports, deduplicates
  events, compares public shareholding snapshots and highlights multi-investor
  overlap. Use for questions about promoter buying/selling, insider disclosures,
  Kedia, Jhunjhunwala, Dolly Khanna, Ashish Kacholia, Mukul Agrawal, bulk deals,
  block deals or changes in shareholding.
argument-hint: "[symbol/person/date range] [--report|--overlaps]"
---

# trader-insider - Indian Insider, Promoter & Super-Investor Tracker

This skill monitors **public disclosures** in Indian listed equities. It is a
research input for the trading workflow, not a copy-trading system. It does not
claim that a disclosed transaction is current, profitable, informed or a buy
signal.

## Coverage

1. **Insider transactions:** disclosures under SEBI (Prohibition of Insider
   Trading) Regulations, 2015, including relevant promoter/promoter-group,
   director and designated-person disclosures published by exchanges.
2. **Substantial acquisition/disposal:** disclosures under SEBI (Substantial
   Acquisition of Shares and Takeovers) Regulations, 2011 (SAST).
3. **Bulk deals:** exchange-published large client transactions.
4. **Block deals:** exchange-published block-window transactions.
5. **Super-investor tracking:** public shareholding snapshots and transaction
   disclosures for a configurable watchlist.
6. **Quarterly shareholding patterns:** promoter/promoter group, public,
   institutional and other reported categories, including pledge data where
   the source provides it.

Politician asset declarations are intentionally out of scope. They are not a
reliable, transaction-level feed for this market-monitoring workflow.

## Official source hierarchy

Use exchange filings as the source of truth:

- NSE insider trading archive: https://www.nseindia.com/companies-listing/corporate-filings-insider-trading-archive-data
- NSE company insider disclosures: https://www.nseindia.com/companies-listing/corporate-filings-insider-trading
- NSE shareholding patterns: https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern
- NSE bulk/block deal and market data: https://www.nseindia.com/market-data
- BSE corporate filings: https://www.bseindia.com/corporates.html
- SEBI PIT Regulations (latest consolidated version): https://www.sebi.gov.in/legal/regulations/mar-2025/securities-and-exchange-board-of-india-prohibition-of-insider-trading-regulations-2015-last-amended-on-march-12-2025-_92672.html
- SEBI SAST Regulations (latest consolidated version): https://www.sebi.gov.in/legal/regulations/dec-2025/securities-and-exchange-board-of-india-substantial-acquisition-of-shares-and-takeovers-regulations-2011-last-amended-on-december-5-2025-_98643.html

Third-party investor portfolio sites may be used only to discover a lead or
name variant. Verify every material claim against an official exchange filing
or company filing before reporting it as confirmed.

## Regulatory interpretation guardrails

- Under PIT Regulation 7(2), the relevant disclosure obligation is triggered
  when aggregate traded value in a calendar quarter exceeds ₹10 lakh, with the
  prescribed disclosure timeline generally expressed as two trading days.
  The applicable persons, recipient and filing sequence depend on the exact
  sub-regulation and current consolidated text. Do not label every trade by an
  employee or every public-market trade as an insider disclosure.
- Under SAST Regulation 29, the initial 5% threshold and subsequent change
  disclosures are subject to the current regulation's exact wording, persons
  acting in concert and applicable exemptions. The familiar 2% change rule
  applies to holders already at/above the relevant threshold; do not apply it
  indiscriminately to all shareholders. Re-check the current consolidated SEBI
  text before stating a legal conclusion.
- A disclosure is not proof of unlawful insider trading. Use neutral language
  such as "reported insider transaction" or "promoter disclosure".
- Distinguish trade/transaction date, exchange filing date and dissemination
  timestamp. Never substitute one for another.
- A filing can be revised or corrected. Preserve revision remarks and prefer
  the latest valid filing while retaining an audit trail.
- Bulk and block deals identify disclosed client-side transactions but do not
  automatically establish that the participant is an insider or a super-investor.

## Super-investor watchlist

The default starter list is configurable and includes Vijay Kedia, Rakesh
Jhunjhunwala (historical/legacy tracking only), Rekha Jhunjhunwala, Dolly
Khanna, Ashish Kacholia and Mukul Agrawal. Treat aliases, family entities,
partnerships and investment vehicles carefully: match only when the filing
supports the association. Never assume that a similarly named entity belongs
to an investor.

Track:

- first-seen visibility in the imported dataset (not proof of a newly opened position);
- increases/decreases in disclosed holdings between two comparable dated snapshots;
- reported buy/sell transactions where a transaction-level filing exists;
- holdings shared by two or more tracked investors;
- the date and period represented by every snapshot.

**Snapshot warning:** quarterly shareholding data is point-in-time data, not a
trade blotter. A change between snapshots does not reveal the exact trade date,
price, number of intervening trades or motive. A name disappearing from a
publicly displayed list does not prove a complete exit; it may reflect reporting
format, category changes, aggregation or a disclosure threshold. Mark such
cases "not visible in latest snapshot", not "sold out".

## Data workflow

The script is standard-library Python. It normalizes CSV/JSON exports saved
from official exchange pages. It does not depend on undocumented NSE/BSE APIs,
replay cookies, bypass access controls or automate interactive downloads.

```bash
python3 "${SKILL_DIR}/insider.py" --help
python3 "${SKILL_DIR}/insider.py" watchlist
python3 "${SKILL_DIR}/insider.py" watchlist --add "Investor Name"

# Import downloaded official CSV/JSON exports
python3 "${SKILL_DIR}/insider.py" import --kind insider --exchange NSE --file insider.csv
python3 "${SKILL_DIR}/insider.py" import --kind sast --exchange BSE --file sast.csv
python3 "${SKILL_DIR}/insider.py" import --kind bulk --exchange NSE --file bulk.csv
python3 "${SKILL_DIR}/insider.py" import --kind block --exchange NSE --file block.csv
python3 "${SKILL_DIR}/insider.py" import --kind shareholding --exchange NSE --file shareholding.csv

# Research and overlap reports
python3 "${SKILL_DIR}/insider.py" report --since 2026-10-01
python3 "${SKILL_DIR}/insider.py" report --symbol ABC --json
python3 "${SKILL_DIR}/insider.py" report --person "Vijay Kedia"
python3 "${SKILL_DIR}/insider.py" changes
python3 "${SKILL_DIR}/insider.py" overlaps --minimum 2
```

Records are appended to `trader/state/insider_events.jsonl`; duplicates are
skipped using a stable event fingerprint. The watchlist is stored at
`trader/insider_watchlist.json`. Keep source exports and source URLs alongside
the event log so every result can be audited. Check `DATA_SOURCES.md` for
field mapping, limitations and source collection steps.

## Reporting format

For each event show: company, symbol/ISIN, person/entity, category, action,
quantity/value or post-transaction holding, transaction/as-on date, filing or
dissemination date, exchange, regulation/deal type, source link and revision
status. Explicitly say when a field is unavailable.

Classify evidence:

- **Confirmed transaction disclosure:** official transaction-level filing.
- **Exchange deal print:** official bulk/block deal record; participant may not
  be an insider or watchlist investor.
- **Snapshot change:** difference between two dated shareholding snapshots;
  transaction date and price are unknown.
- **Third-party lead:** unverified until matched to an official filing.

For snapshot comparisons, show both as-on dates and the change in shares/percentage where both are available. For multi-investor overlap, show the snapshot dates and holding percentages.
Do not imply coordinated buying or same-day accumulation.

## Trading workflow integration

These events are context for the scanner, never a standalone entry trigger.
Check liquidity, price/volume behaviour, trend, corporate announcements and
risk rules. A stale disclosure may be useful for research but is not evidence
of a trade today. The system only shortlists candidates; the user makes the
trade decision and executes manually.

## What it never does

- Never calls a disclosure illegal insider trading or claims the person had
  unpublished price-sensitive information.
- Never invents a trade date, price, quantity, exact amount or investor link.
- Never turns a quarterly snapshot into an exact transaction.
- Never treats bulk/block deal participants as insiders without evidence.
- Never places orders or copies trades automatically.
- Never bypasses exchange access controls or relies on undocumented endpoints.

## Safety

Educational research and decision support, not financial advice. No trade is
placed by this skill. All candidate trades pass through the existing scanner,
entry, budget and risk workflow, with manual execution by the user.
