# Google Device Price Tracker

A daily-updating price comparison site for Google-made devices (Pixel phones,
Pixel Watch, Pixel Buds, Fitbit, Nest/Google Home) across five markets:
**Japan, India, Australia, Singapore, Taiwan**.

Live site (once deployed, see below): `https://<your-github-username>.github.io/google-device-price-tracker/`

## How it works

```
scraper/
  markets.py       market config (locales, currencies, store paths)
  google_store.py  scrapes store.google.com for every product's live catalog + price
  competitors.py   best-effort competitor/retail-platform pricing
  run.py           orchestrates both, writes data/latest.json + data/history/<date>.json
site/
  index.html / app.js / styles.css   static frontend, reads data/latest.json
.github/workflows/daily-price-update.yml   runs the scraper daily, commits data, deploys to Pages
```

No external dependencies — the scraper uses only the Python standard library
(`urllib`), so there's nothing to `pip install` in CI.

### Why this is reliable: Google Store's own structured data

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

Most regional retail sites (Amazon on every domain, Flipkart, Lazada,
Shopee, Croma, JB Hi-Fi, Officeworks, …) either render prices client-side
via JS or actively block plain automated requests (Cloudflare/Akamai/
PerimeterX challenges, 403s, 429s on repeated/scheduled traffic). Building
a scraper that fights those defenses daily, unattended, is both fragile and
arguably against several of these sites' Terms of Service.

So `competitors.py` is honest about what it can and can't do:

- **Taiwan / PChome 24h** has a plain public JSON search endpoint
  (`ecshweb.pchome.com.tw/search/v3.3/...`) that works today with no
  auth — this is a real, live scrape (`method: "scraped"`).
- **Everything else** gets a direct search deep-link to that platform
  instead of an invented price (`method: "search_link"`). The site renders
  these as "Check price →" rather than a number.
- **Optional upgrade path**: if you set a `SERPAPI_KEY` secret (from
  [serpapi.com](https://serpapi.com), paid), `competitors.py` will call its
  Google Shopping engine per-market and get real, JS-rendered marketplace
  offers everywhere — this is the realistic way to get full automated
  competitor coverage in every market without each site's anti-bot defenses
  being a daily maintenance burden. Without the key, those calls are simply
  skipped.
- Other on-ramps worth knowing about: Rakuten Japan has an official free
  **Ichiba Item Search API** (needs a `RAKUTEN_APP_ID`), and Amazon has the
  (partner-gated) **Product Advertising API** / the paid **Rainforest API**.
  None of these are wired up yet — `competitors.py`'s docstring shows where
  to add them.

## Running it yourself

```bash
python3 scraper/run.py            # scrapes all 5 markets, writes data/latest.json
python3 -m http.server 8000       # serve the repo root
open http://localhost:8000/site/index.html
```

Scrape a single market while developing:

```bash
python3 scraper/google_store.py au   # prints JSON for Australia only
python3 scraper/competitors.py "Pixel 11 Pro"   # test the Taiwan PChome matcher
```

## Deploying

This repo was built locally and has **not** been pushed to GitHub yet. To
get the daily automation and a public (or private) URL running:

1. Create a new GitHub repository and push this folder to it.
2. In the repo's **Settings → Pages**, set Source to **GitHub Actions**.
3. (Optional) Add a `SERPAPI_KEY` repository secret under **Settings →
   Secrets and variables → Actions** to unlock full automated competitor
   pricing everywhere, not just Taiwan.
4. The `daily-price-update.yml` workflow runs once a day (18:00 UTC), plus
   on-demand via the Actions tab's "Run workflow" button. It re-scrapes,
   commits the refreshed `data/`, and redeploys the Pages site.

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
- **Competitor coverage is uneven by design** — see above. Treat "Check
  price →" links as exactly that: a shortcut to go compare yourself, not a
  missing data point to be alarmed about.
- **Discount/markup badges** compare a competitor's price against the
  Google Store "from" price for the same product *family*, not necessarily
  the identical colour/storage/condition — a cheap competitor hit could be
  a different storage tier or a refurbished listing. Always check before
  trusting a badge for a purchase decision.
