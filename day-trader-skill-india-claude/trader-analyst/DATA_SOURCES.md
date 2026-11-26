# trader-analyst India data-source notes

- Primary: NSE Integrated Filing – Financials and NSE Financial Results.
- Secondary: BSE Corporate Financial Results / Listing Centre filings.
- Supplementary: issuer investor-relations sites.
- Official exchange XBRL attachments are retained locally and parsed by facts.py.
- No undocumented endpoint, third-party data provider, cookie replay or access-control bypass is used.
- Manifest entries are required to identify ticker, ISIN where known, basis, period end, filing date and source.
- The parser is a first-pass common-concept normaliser. Industry-specific taxonomy mapping and live source retrieval remain explicit follow-up work.
