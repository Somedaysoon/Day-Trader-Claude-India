# Trader Insider - source and field guide

## Primary official sources

| Dataset | Primary source | Use |
|---|---|---|
| PIT/insider disclosures | [NSE insider archive](https://www.nseindia.com/companies-listing/corporate-filings-insider-trading-archive-data) and [company-level page](https://www.nseindia.com/companies-listing/corporate-filings-insider-trading) | Reported transactions by covered persons |
| Shareholding patterns | [NSE shareholding patterns](https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern) | Quarterly snapshots and category changes |
| SAST disclosures | NSE/BSE corporate filings; [BSE corporate filings](https://www.bseindia.com/corporates.html) | Substantial acquisition/disposal and related disclosures |
| Bulk/block deals | NSE market data and BSE corporate/market disclosures | Exchange-published large transactions |
| Regulatory text | [SEBI PIT Regulations, consolidated to 12 Mar 2025](https://www.sebi.gov.in/legal/regulations/mar-2025/securities-and-exchange-board-of-india-prohibition-of-insider-trading-regulations-2015-last-amended-on-march-12-2025-_92672.html); [SEBI SAST Regulations, consolidated to 5 Dec 2025](https://www.sebi.gov.in/legal/regulations/dec-2025/securities-and-exchange-board-of-india-substantial-acquisition-of-shares-and-takeovers-regulations-2011-last-amended-on-december-5-2025-_98643.html) | Verify regulatory interpretation |

## Collection protocol

1. Search NSE/BSE by company, symbol, investor/person and date range.
2. Download the official CSV/XBRL/PDF or save the official filing URL.
3. If XBRL is supplied, retain the original file and convert/extract fields without
   discarding the original tags or filing metadata.
4. Import a CSV or JSON export with `insider.py import` and the appropriate kind.
5. Retain the original source file, URL, exchange, filing date and revision notes.
6. For a material event, open the original filing and verify the person, security,
   transaction type, quantity, date and whether the filing is revised.

No unofficial endpoint, cookie replay, login automation or access-control
workaround is part of this workflow. If an exchange page requires interactive
navigation or the data export is unavailable, retrieve it manually through the
official page and import the saved export.

## Normalized fields

The importer maps common CSV labels to these normalized fields:

- Identity: `person`, `category`, `company`, `symbol`, `isin`, `exchange`
- Event: `kind`, `action`, `regulation`, `remarks`
- Transaction: `quantity`, `price`, `value`, `trade_date`, `filing_date`
- Snapshot: `holding_shares`, `holding_pct`, `as_on_date`
- Provenance: `source_url`

The importer is intentionally permissive about column labels, but it cannot
reliably interpret every exchange-specific export or nested XBRL structure.
Review the normalized JSON and original file. Missing source URLs are flagged.

## Data-quality rules

- Do not infer a transaction price from the closing market price.
- Do not use a filing/dissemination date as the trade date.
- Do not add NSE and BSE quantities/values together unless the filing or
  methodology confirms that they are distinct executions rather than duplicate
  reporting of the same event.
- Keep pledge creation, invocation and release distinct from outright sale.
- Keep buy, sale, gift, inheritance, allotment, conversion, encumbrance and
  off-market transfers distinct where the source provides those details.
- Preserve revisions. Do not silently overwrite an earlier disclosure.
- Map entities and aliases to an investor only with documentary support.
- Quarterly holdings may be incomplete for investors whose positions are not
  individually shown in the public format. Absence is not proof of exit.

## Regulatory caution

The ₹10 lakh / two-trading-day PIT summary and SAST threshold summaries in the
skill are operational prompts, not a legal opinion. Consult the latest SEBI
consolidated regulation and exchange circular before interpreting an individual
filing, deadline, exemption or breach. The regulatory pages may be amended after
the versions linked above.
