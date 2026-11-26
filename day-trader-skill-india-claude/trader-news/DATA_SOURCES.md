# Trader News - Indian data sources

## Preconfigured official RSS feeds

| Publisher | Feed | Use |
|---|---|---|
| RBI | `https://rbi.org.in/pressreleases_rss.xml` | Monetary policy, banking and financial-sector announcements |
| RBI | `https://rbi.org.in/notifications_rss.xml` | Regulatory notifications |
| SEBI | `https://www.sebi.gov.in/sebirss.xml` | Press releases, circulars, orders and rulings |
| PIB | `https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1` | Government policy and economic announcements |

These URLs are listed by the respective publishers' RSS pages. Feeds may change or be delayed; a failed feed is reported as a note and is not silently treated as a successful check.

## NSE feeds

Use the official NSE RSS directory: <https://www.nseindia.in/static/rss-feed>. Copy the RSS URL for the required feed (at minimum Corporate Announcements; optionally Financial Results, Board Meetings and Circulars) into the relevant blank `url` field in `trader/news_feeds.json`.

The project intentionally does not guess undocumented NSE endpoints, replay browser cookies, or bypass exchange access controls.

## BSE announcements

Download the required Corporate Announcements CSV manually from BSE's official corporate-announcements page and pass it to `news.py --bse-csv PATH`. The importer recognizes common column-name variants and leaves the original file untouched. If BSE changes its CSV column layout, inspect the header and update the importer mapping before relying on the import.

BSE source page: <https://www.bseindia.com/corporates/ann.html>

## Price context

Price-move context uses Yahoo Finance's chart endpoint for the last price versus previous close. Plain symbols are treated as NSE symbols and suffixed `.NS`; explicit `.BO` symbols are retained for BSE securities. This is supplementary, unofficial quote data and can lag. Verify live prices on the broker/exchange before acting.

## Interpretation

- Headlines and RSS summaries are leads. Open the linked original filing or release before relying on it.
- A regulator or government announcement is not automatically company-specific; only explicit symbol/company matches are labelled as touching a watchlist stock.
- Broad macro/regulatory items can still appear without a ticker match.
- News is context for human review, not a standalone entry/exit trigger.
