# Google Device Price Tracker

A weekly-updating price comparison site for Google-made devices (Pixel phones,
Pixel Watch, Pixel Buds, Fitbit, Nest/Google Home) across five markets:
**Japan, India, Australia, Singapore, Taiwan**.

Live site: **https://bihimin.github.io/google-device-price-tracker/**
Repo: **https://github.com/bihimin/google-device-price-tracker**

## How it works

```
scraper/
  markets.py       market config (locales, currencies, store paths)
  google_store.py  scrapes store.google.com for every product's live catalog + price
  competitors.py   real scrapes (Amazon + Taiwan sites) + best-effort fallback links
  run.py           orchestrates both, writes data/latest.json + data/history/<date>.json
site/
  index.html / app.js / styles.css   static frontend, reads data/latest.json
.github/workflows/weekly-price-update.yml   runs the scraper weekly, commits data, deploys to Pages
```

No external dependencies — the scraper uses only the Python standard library
(`urllib`), so there's nothing to `pip install` in CI.

### Why Google Store scraping is reliable: its own structured data

`store.google.com` product pages are server-rendered with
[schema.org](https://schema.org/Product) `Product`/`AggregateOffer` microdata
embedded directly in the HTML (Google uses this for its own rich search
snippets). That means a plain `GET` + regex parse is enough to read every
colour/storage SKU and its exact price, in any locale — no headless browser,
no JS execution, no API key. This is what `google_store.py` does:

1. `discover_slugs(market)` fetches a handful of category pages
   (`/category/phones`, `/category/watches_trackers`, etc.) per market and
   harvests every `/config/<slug>` and `/product/<slug>` link they contain —
   which in practice covers nearly the entire catalog Google Store sells in
   that market, current-gen and still-listed previous-gen alike.
2. For phones (`/config/...` pages), each colour+storage combination is its
   own `AggregateOffer` with an exact price and stock status
   (`InStock`/`OutOfStock`/`PreOrder`).
3. For watches/buds/Nest/Fitbit (`/product/...` pages), there's one
   aggregate "from" price plus a list of `data-product-docid` values that
   enumerate the SKU variants (case size, connectivity, colour) sold at
   that price.

### Competitor pricing: what's real vs. a search link

Most regional retail sites either render prices client-side via JS or sit
behind an active bot-defense layer (Cloudflare/Akamai/Imperva) that blocks
plain automated requests outright. But a surprising number of **major**
retailers still server-render their search results with the price baked
into the HTML for their own SEO rich-snippets — Amazon chief among them, on
every regional domain checked here.

Real, live scrapes (`method: "scraped"`), no login, no JS:

- **Amazon** — `.in`, `.com.au`, `.co.jp`, `.sg`. Title lives in an
  `<h2 aria-label=...>`, price in an `a-offscreen` span, both inside the
  same `s-search-result` card, so a plain GET + regex pulls real listings.
  The tricky part: without an explicit currency preference, Amazon guesses
  your currency from the requester's *IP*, which for a GitHub Actions
  runner is essentially never actually in that country (e.g. amazon.co.jp
  silently serving SGD-denominated prices). Setting the `i18n-prefs`
  cookie to the market's currency (`JPY`/`INR`/`AUD`/`SGD`) fixes this
  regardless of where the scrape physically runs.
- **Taiwan / PChome 24h** — a plain public JSON search endpoint
  (`ecshweb.pchome.com.tw/search/v3.3/...`).
- **Taiwan / Yahoo!奇摩購物中心** — its search page embeds a full
  schema.org `ItemList` of `Product`/`Offer` JSON-LD.

For every match, a product name alone (e.g. "Pixel 11") isn't enough to
trust the first search hit — it'll happily match a screen protector or a
different storage tier at a similar price. Each scraper also knows Google
Store's own price for that product and only accepts a candidate within
roughly 0.35×–2.5× of it, picking the closest match; if nothing on the
page clears that bar (which does happen — some markets' marketplaces
genuinely don't list the device, just cases for it), it reports no match
rather than a confidently wrong one.

**Everything else** gets a direct search deep-link to that platform instead
of an invented price (`method: "search_link"`) — rendered as
"Check price →" rather than a number. This list is capped at **5
platforms per product per market** (`MAX_PLATFORMS_PER_MARKET` in
`competitors.py`), scraped sources filling slots first.

**Optional upgrade path**: if you set a `SERPAPI_KEY` repository secret
(from [serpapi.com](https://serpapi.com), paid beyond its free tier —
expect to need the ~$25–75/month tier for full weekly coverage across all
5 markets, since the free tier is only ~100–250 searches/month),
`competitors.py` calls its Google Shopping engine for any product that
still has an open slot after the real scrapes above, covering retailers
like Flipkart, JB Hi-Fi, Shopee, Rakuten, etc. that block plain scraping.
Without the key, those calls are simply skipped — nothing breaks.

## Running it yourself

```bash
python3 scraper/run.py            # scrapes all 5 markets, writes data/latest.json
python3 -m http.server 8000       # serve the repo root
open http://localhost:8000/site/index.html
```

Scrape a single market/product while developing:

```bash
python3 scraper/google_store.py au                 # prints JSON for Australia only
python3 scraper/competitors.py in "Pixel 11 Pro"   # test the Amazon India matcher
```

## Deployment status

Live on GitHub Pages, deployed via GitHub Actions (`workflow: Weekly price
update`). The workflow:

- Runs every Monday at 18:00 UTC, or on-demand from the Actions tab's
  "Run workflow" button.
- Re-scrapes all 5 markets, commits the refreshed `data/`, and redeploys
  the Pages site.
- Weekly (not daily) by design — device prices don't move daily, and it's
  gentler on the retail sites being scraped. Bump the cron in
  `.github/workflows/weekly-price-update.yml` to `"0 18 * * *"` for daily
  if you want fresher data and are comfortable with the extra request
  volume.

## Known limitations

- **SKU granularity for wearables**: Pixel Watch/Fitbit/Nest pricing is
  captured as a single "from" price per product family, not broken out by
  every band colour (Google Store usually doesn't price-differentiate by
  band colour, only by case size/connectivity — those are enumerated in
  `sku_docids` but not individually priced).
- **Discontinued SKUs**: pages for a fully retired product (e.g. the
  original Pixel Tablet in some markets) return Google Store's generic
  homepage rather than product data, so they're silently skipped rather
  than reported as "$0" or similar.
- **Competitor coverage is uneven by design** — a market's biggest
  marketplace (Amazon) not listing a specific device is real signal, not a
  bug; and sites behind Cloudflare/Akamai/Imperva (Croma, Reliance
  Digital, Harvey Norman, momo's API, Rakuten's search page) are
  deliberately left as links rather than fought with daily automated
  requests. Treat "Check price →" links as exactly that: a shortcut to go
  compare yourself, not a missing data point to be alarmed about.
- **Discount/markup badges** compare a competitor's price against the
  Google Store "from" price for the same product *family*, not necessarily
  the identical colour/storage/condition — a cheap competitor hit could be
  a different storage tier or a refurbished listing. Always check before
  trusting a badge for a purchase decision.
- **Scraping Amazon et al. programmatically sits in a grey area of most
  such sites' Terms of Service**, even though the data scraped here is
  public search-result HTML, not account data, and the cadence is weekly
  and lightweight (a handful of requests per product, once a week) rather
  than high-volume or commercial. If that matters for your use of this
  project, review the relevant site's ToS yourself before relying on it.
