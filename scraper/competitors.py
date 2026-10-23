"""Competitor / retail-platform pricing, per market.

Reality check, documented here instead of hidden in a bug report later:
most regional retail sites (Amazon on every domain, Flipkart, Lazada,
Shopee, Croma, JB Hi-Fi, ...) actively block plain automated HTTP requests
(Cloudflare/Akamai/PerimeterX challenges, 403s, 429s) and/or render prices
client-side via JS. Scraping them reliably needs a real browser session
re-run daily, which is both fragile to maintain here and against several of
these sites' Terms of Service when done as unattended, scheduled scraping.

So this module ships two tiers per market:

1. `scrape_*` functions: sources that expose a plain server-side JSON/HTML
   response we can call directly and that we've verified actually works
   (currently: PChome Taiwan's public search endpoint). These produce a
   real `price` field.
2. Deep-link fallbacks: for every other platform, we build a direct search
   URL for the product instead of inventing a price. The site renders this
   as "Compare on <Platform> ->" rather than a number.

To upgrade a fallback to a real scrape later:
  - Drop in a paid SERP/price API (e.g. SerpApi's Google Shopping engine,
    Rainforest API for Amazon, Keepa for Amazon price history) behind an
    env var (see `_serpapi_price` below for the shape), or
  - Register for an official retailer API where one exists (e.g. Rakuten
    Ichiba's Item Search API needs a free `RAKUTEN_APP_ID`), or
  - Write a `scrape_<platform>` function once you've confirmed (as this
    file's comments show, per-site) that the platform's search response is
    reachable without a browser.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _get(url: str, headers: dict | None = None) -> str | None:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def _clean_name(raw: str) -> str:
    name = re.sub(
        r"^(Buy|Discover the new|Explore|認識全新|購買搭載[^的]*的|立即購買)\s*",
        "",
        raw,
    ).strip()
    name = name.split(" with ")[0].split(" in ")[0].strip()
    return re.sub(r"^Google\s+", "", name).strip()


def _lowest_price(product: dict) -> float | None:
    if product.get("type") == "config":
        variants = product.get("variants") or []
        in_stock = [v for v in variants if v.get("availability") == "InStock"]
        pool = in_stock or variants
        prices = [v["price"] for v in pool if v.get("price") is not None]
        return min(prices) if prices else None
    return product.get("from_price")


def _product_names(google_store_products: list[dict]) -> list[tuple[str, float | None]]:
    """Collapse the Google Store product list down to one search term + a
    reference price per device family (e.g. 'Pixel 11', not every
    colour/storage SKU)."""
    names: list[tuple[str, float | None]] = []
    seen = set()
    for p in google_store_products:
        name = _clean_name(p["name"])
        if name and name not in seen:
            seen.add(name)
            names.append((name, _lowest_price(p)))
    return names


# ---------------------------------------------------------------------------
# Tier 1: real scrapers (verified working against a plain HTTP GET)
# ---------------------------------------------------------------------------

def scrape_pchome_tw(product_name: str, expected_price: float | None = None) -> dict | None:
    """PChome 24h (Taiwan) public search JSON API. No auth, no JS needed."""
    q = urllib.parse.quote(product_name)
    url = f"https://ecshweb.pchome.com.tw/search/v3.3/all/results?q={q}&page=1&sort=sale/dc"
    body = _get(url)
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    prods = data.get("prods") or []
    accessory_words = (
        "殼", "貼", "鋼化", "玻璃", "充電", "傳輸線", "支架",
        "耳機", "套", "掛繩", "包膜", "維修", "保護",
    )
    query_tokens = [t for t in product_name.split() if t.isascii()]
    phone_like = [
        p for p in prods
        if not any(w in (p.get("name") or "") for w in accessory_words)
        and p.get("price", 0) > 5000
        and all(tok.lower() in (p.get("name") or "").lower() for tok in query_tokens)
    ]
    # A short query like "Pixel 11" is a substring of "Pixel 11 Pro Fold" too,
    # so token-matching alone isn't enough -- prefer whichever candidate's
    # price is closest to what Google Store charges for this product.
    if expected_price:
        phone_like = [
            p for p in phone_like
            if 0.4 * expected_price <= p.get("price", 0) <= 2.2 * expected_price
        ] or phone_like
        phone_like.sort(key=lambda p: abs(p.get("price", 0) - expected_price))
    if not phone_like:
        return None
    best = phone_like[0]
    return {
        "platform": "PChome 24h",
        "matched_title": best.get("name"),
        "price": best.get("price"),
        "original_price": best.get("originPrice"),
        "url": f"https://24h.pchome.com.tw/prod/{best['Id']}" if best.get("Id") else url,
        "method": "scraped",
    }


def _serpapi_price(product_name: str, google_domain: str, gl: str, hl: str) -> dict | None:
    """Optional: if SERPAPI_KEY is set, use SerpApi's Google Shopping engine
    to get real, JS-rendered marketplace offers for any market. Unused
    unless the env var is present -- this is the easy on-ramp to full
    automation once you're willing to pay for a SERP API."""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return None
    params = {
        "engine": "google_shopping",
        "q": product_name,
        "google_domain": google_domain,
        "gl": gl,
        "hl": hl,
        "api_key": api_key,
    }
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    body = _get(url)
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    results = data.get("shopping_results") or []
    if not results:
        return None
    best = results[0]
    return {
        "platform": best.get("source", "Google Shopping"),
        "matched_title": best.get("title"),
        "price": best.get("extracted_price"),
        "url": best.get("product_link") or best.get("link"),
        "method": "serpapi",
    }


# ---------------------------------------------------------------------------
# Tier 2: deep-link fallbacks (platform search URL, no scraped price)
# ---------------------------------------------------------------------------

FALLBACK_PLATFORMS = {
    "jp": [
        ("Amazon Japan", "https://www.amazon.co.jp/s?k={q}"),
        ("Rakuten Ichiba", "https://search.rakuten.co.jp/search/mall/{q}/"),
        ("Yodobashi Camera", "https://www.yodobashi.com/?word={q}"),
        ("Biccamera", "https://www.biccamera.com/bc/result/?q={q}"),
    ],
    "in": [
        ("Amazon India", "https://www.amazon.in/s?k={q}"),
        ("Flipkart", "https://www.flipkart.com/search?q={q}"),
        ("Croma", "https://www.croma.com/searchB?q={q}%3Arelevance"),
        ("Reliance Digital", "https://www.reliancedigital.in/search?q={q}"),
    ],
    "au": [
        ("Amazon Australia", "https://www.amazon.com.au/s?k={q}"),
        ("JB Hi-Fi", "https://www.jbhifi.com.au/search?q={q}"),
        ("Officeworks", "https://www.officeworks.com.au/shop/officeworks/search?q={q}"),
        ("Harvey Norman", "https://www.harveynorman.com.au/catalogsearch/result/?q={q}"),
    ],
    "sg": [
        ("Shopee Singapore", "https://shopee.sg/search?keyword={q}"),
        ("Lazada Singapore", "https://www.lazada.sg/catalog/?q={q}"),
        ("Challenger", "https://www.challenger.sg/catalogsearch/result/?q={q}"),
        ("Courts", "https://www.courts.com.sg/catalogsearch/result/?q={q}"),
    ],
    "tw": [
        ("momo購物網", "https://www.momoshop.com.tw/search/searchShop.jsp?keyword={q}"),
        ("Yahoo奇摩購物中心", "https://tw.buy.yahoo.com/search/product?p={q}"),
        ("Pchome 商店街", "https://shopping.pchome.com.tw/search/v1.3/all?q={q}"),
    ],
}

SERPAPI_LOCALE = {
    "jp": ("google.co.jp", "jp", "ja"),
    "in": ("google.co.in", "in", "en"),
    "au": ("google.com.au", "au", "en"),
    "sg": ("google.com.sg", "sg", "en"),
    "tw": ("google.com.tw", "tw", "zh-tw"),
}


def scrape_market(market_code: str, google_store_products: list[dict]) -> list[dict]:
    names = _product_names(google_store_products)
    offers = []

    for name, expected_price in names:
        entry = {"product": name, "offers": []}

        if market_code == "tw":
            scraped = scrape_pchome_tw(name, expected_price)
            if scraped:
                entry["offers"].append(scraped)

        domain, gl, hl = SERPAPI_LOCALE[market_code]
        serp = _serpapi_price(name, domain, gl, hl)
        if serp:
            entry["offers"].append(serp)

        for platform, template in FALLBACK_PLATFORMS.get(market_code, []):
            already_covered = any(o["platform"] == platform for o in entry["offers"])
            if already_covered:
                continue
            entry["offers"].append(
                {
                    "platform": platform,
                    "url": template.format(q=urllib.parse.quote(name)),
                    "method": "search_link",
                }
            )

        offers.append(entry)

    return offers


if __name__ == "__main__":
    import sys

    result = scrape_pchome_tw(sys.argv[1] if len(sys.argv) > 1 else "Pixel 11")
    print(json.dumps(result, indent=2, ensure_ascii=False))
