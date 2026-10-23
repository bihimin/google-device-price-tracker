"""Competitor / retail-platform pricing, per market.

Reality check, documented here instead of hidden in a bug report later:
most regional retail sites either render prices client-side via JS or sit
behind an active bot-defense layer (Cloudflare/Akamai/Imperva) that blocks
plain automated requests outright. Scraping those reliably needs a real,
re-authenticated browser session every run, which is both fragile to
maintain unattended and arguably against several of those sites' Terms of
Service when done as scheduled, automated scraping.

That said, a surprising number of *major* retailers still server-render
their search results with the price baked into the HTML (for their own SEO
rich-snippets) -- Amazon chief among them, on every regional domain we
checked. Those get a real, live scrape here. For everything else, we build
a direct search deep-link to that platform instead of inventing a price.

Verified working as a plain HTTP GET (UA header + gzip decoding, no
JS, no login):
  - Amazon (.in / .com.au / .co.jp / .sg) search results -- `scrape_amazon`
  - PChome 24h (Taiwan) public search JSON API -- `scrape_pchome_tw`
  - Yahoo!奇摩購物中心 (Taiwan) search page JSON-LD -- `scrape_yahoo_tw`

Everything else in FALLBACK_PLATFORMS is a deep link, not a scrape --
either because it's behind Cloudflare/Akamai/Imperva bot challenges (Croma,
Reliance Digital, Harvey Norman, momo's API, Rakuten's search page), is a
pure client-rendered SPA with no server-side price (JB Hi-Fi, Lazada,
Shopee), or we simply haven't verified it. Rendering shows "Check price ->"
for those instead of a number.

To upgrade a fallback to a real scrape later:
  - Drop in a paid SERP/price API (e.g. SerpApi's Google Shopping engine,
    Rainforest API for Amazon) behind an env var -- see `_serpapi_price`,
    or
  - Register for an official retailer API where one exists (e.g. Rakuten
    Ichiba's Item Search API needs a free `RAKUTEN_APP_ID`), or
  - Write a `scrape_<platform>` function once you've confirmed (as the
    three above show, per-site) that the platform's response is reachable
    without a browser.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEBUG = bool(os.environ.get("DEBUG_SCRAPE"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

MAX_PLATFORMS_PER_MARKET = 5


def _get(url: str, headers: dict | None = None, debug: bool = False) -> str | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            text = raw.decode("utf-8", errors="ignore")
            if debug:
                print(f"[_get] {url} -> {resp.status}, {len(text)} chars", file=sys.stderr)
            return text
    except Exception as e:
        if debug:
            print(f"[_get] {url} -> FAILED: {e!r}", file=sys.stderr)
        return None


def _clean_name(raw: str) -> str:
    name = re.sub(
        r"^(Buy|Discover the new|Explore|認識全新|購買搭載[^的]*的|立即購買|購買)\s*",
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


def _closest_by_price(candidates: list[dict], price_key, expected_price: float | None) -> list[dict]:
    """Shared heuristic: a short query ('Pixel 11') matches accessories and
    sibling SKUs too, so once we have Google Store's own price for this
    product, prefer whichever candidate is closest to it instead of just
    the first search result. When we *do* have a reference price, a result
    page with nothing in a plausible range (e.g. a market where this
    product only turns up accessories) should report no match at all
    rather than confidently return an accessory as if it were the phone.
    Without a reference price there's nothing to validate against, so the
    raw candidate list (already keyword-filtered) is returned as-is."""
    if not candidates:
        return candidates
    if expected_price:
        in_range = [
            c for c in candidates
            if 0.35 * expected_price <= price_key(c) <= 2.5 * expected_price
        ]
        return sorted(in_range, key=lambda c: abs(price_key(c) - expected_price))
    return candidates


# Qualifier words that distinguish sibling SKUs/models at a similar price
# point ("Pixel 10" vs "Pixel 10 Pro", "Pixel Watch 5" vs "... Max"). Used
# so a bare "Pixel 10" query won't accept a "Pixel 10 Pro XL" listing just
# because every character of "10" happens to appear in it.
_QUALIFIER_WORDS = {"pro", "xl", "fold", "max", "ultra", "se", "plus"}


def _title_matches(product_name: str, title: str) -> bool:
    """True if every distinctive token of `product_name` appears in `title`
    as a whole word (so "10" doesn't match inside "10a"), and `title`
    doesn't introduce a qualifier word ("Pro", "XL", "Fold", ...) that
    `product_name` didn't ask for."""
    title_lower = title.lower()
    # Only the ASCII tokens (model name/number) are required to literally
    # appear in the competitor's title -- a Japanese/Chinese descriptor
    # suffix in our own product name (e.g. "... トラッカー") has no reason
    # to appear verbatim in an English-titled listing.
    query_tokens = [
        t for t in re.split(r"\s+", product_name)
        if t and t.lower() != "google" and t.isascii()
    ]
    for tok in query_tokens:
        if not re.search(rf"(?<![a-z0-9]){re.escape(tok.lower())}(?![a-z0-9])", title_lower):
            return False

    query_words = {t.lower() for t in query_tokens}
    title_words = set(re.findall(r"[a-z0-9]+", title_lower))
    if (title_words & _QUALIFIER_WORDS) - query_words:
        return False
    return True


# ---------------------------------------------------------------------------
# Tier 1: real scrapers (verified working against a plain HTTP GET)
# ---------------------------------------------------------------------------

AMAZON_DOMAINS = {
    "jp": "www.amazon.co.jp",
    "in": "www.amazon.in",
    "au": "www.amazon.com.au",
    "sg": "www.amazon.sg",
}

# Without an explicit currency preference, Amazon renders prices in whatever
# currency it guesses from the requester's IP (which, for a cloud CI runner,
# is rarely actually in that country) -- e.g. amazon.co.jp silently showing
# SGD. The `i18n-prefs` cookie pins the currency to the market regardless of
# where the request physically comes from.
AMAZON_CURRENCY = {"jp": "JPY", "in": "INR", "au": "AUD", "sg": "SGD"}

_ACCESSORY_WORDS = (
    "case", "cover", "screen protector", "tempered glass", "charger", "cable",
    "adapter", "strap", "band", "skin", "stand", "holder", "mount", "lens protector",
    "replacement", "repair part", "spare part", "refurb", "flex cable",
    "ケース", "カバー", "フィルム", "ガラス", "充電", "ケーブル", "バンド",
    "レザー", "交換", "修理", "パーツ", "マウント", "スタンド",
    "殼", "貼", "鋼化", "玻璃", "充電", "傳輸線", "支架", "套", "掛繩", "包膜",
    "保護貼", "維修", "manual", "guide", "book", "sim card", "sim only",
)


def scrape_amazon(product_name: str, market_code: str, expected_price: float | None = None) -> dict | None:
    """Amazon's regional search pages server-render results (title in an
    <h2 aria-label=...>, price in a-price-whole spans) well enough for a
    plain GET + regex to work -- no login, no JS. Verified on .in, .com.au,
    .co.jp and .sg."""
    domain = AMAZON_DOMAINS.get(market_code)
    if not domain:
        return None
    q = urllib.parse.quote(product_name)
    url = f"https://{domain}/s?k={q}"
    currency = AMAZON_CURRENCY.get(market_code, "")
    html = _get(url, headers={"Cookie": f"i18n-prefs={currency}"} if currency else None, debug=DEBUG)
    if not html:
        return None
    if DEBUG and ("Sorry" in html[:3000] or "api-services-support@amazon.com" in html or "captcha" in html.lower()):
        print(f"[scrape_amazon] {market_code} '{product_name}': looks like a block/CAPTCHA page ({len(html)} chars)", file=sys.stderr)

    # Split into one block per result card so a title is only ever paired
    # with a price from its own card (not a neighbour's strikethrough price,
    # rating count, or delivery-date digits).
    blocks = re.split(r'data-component-type="s-search-result"', html)[1:]

    candidates = []
    for b in blocks:
        t_m = re.search(r'<h2 aria-label="([^"]+)"', b)
        if not t_m:
            continue
        title = t_m.group(1)
        if any(w.lower() in title.lower() for w in _ACCESSORY_WORDS):
            continue
        if "pixel" not in title.lower() and "fitbit" not in title.lower() and "nest" not in title.lower():
            continue
        if not _title_matches(product_name, title):
            continue
        p_m = re.search(r'a-offscreen">([^<]+)<', b)
        if not p_m:
            continue
        price_raw = re.sub(r"[^\d.]", "", p_m.group(1))
        try:
            price = float(price_raw)
        except ValueError:
            continue
        if price <= 0:
            continue
        candidates.append({"title": title, "price": price})

    if DEBUG:
        print(f"[scrape_amazon] {market_code} '{product_name}': {len(blocks)} blocks, "
              f"{len(candidates)} candidates before price filter", file=sys.stderr)
    candidates = _closest_by_price(candidates, lambda c: c["price"], expected_price)
    if not candidates:
        return None
    best = candidates[0]

    platform_name = {
        "jp": "Amazon Japan", "in": "Amazon India",
        "au": "Amazon Australia", "sg": "Amazon Singapore",
    }[market_code]
    return {
        "platform": platform_name,
        "matched_title": best["title"],
        "price": best["price"],
        "url": url,
        "method": "scraped",
    }


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
    phone_like = [
        p for p in prods
        if not any(w in (p.get("name") or "") for w in accessory_words)
        and p.get("price", 0) > 1000
        and _title_matches(product_name, p.get("name") or "")
    ]
    phone_like = _closest_by_price(phone_like, lambda p: p.get("price", 0), expected_price)
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


def scrape_yahoo_tw(product_name: str, expected_price: float | None = None) -> dict | None:
    """Yahoo!奇摩購物中心 (Taiwan) embeds a full schema.org ItemList with
    per-product Offer/price as JSON-LD on its search results page."""
    q = urllib.parse.quote(product_name)
    url = f"https://tw.buy.yahoo.com/search/product?p={q}"
    html = _get(url)
    if not html:
        return None
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    items = []
    for b in blocks:
        try:
            data = json.loads(b)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            data = [data]
        for entry in data:
            if entry.get("@type") == "ItemList":
                items.extend(entry.get("itemListElement") or [])

    accessory_words = (
        "殼", "貼", "鋼化", "玻璃", "充電", "傳輸線", "支架",
        "耳機", "套", "掛繩", "包膜", "維修", "保護",
    )
    candidates = []
    for it in items:
        p = it.get("item", it)
        name = p.get("name") or ""
        offer = p.get("offers") or {}
        price = offer.get("price")
        if price is None or any(w in name for w in accessory_words):
            continue
        if not _title_matches(product_name, name):
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 1000:
            continue
        candidates.append({"name": name, "price": price, "url": offer.get("url")})

    candidates = _closest_by_price(candidates, lambda c: c["price"], expected_price)
    if not candidates:
        return None
    best = candidates[0]
    return {
        "platform": "Yahoo奇摩購物中心",
        "matched_title": best["name"],
        "price": best["price"],
        "url": best.get("url") or url,
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
# Amazon is deliberately absent here -- it's a real scrape (scrape_amazon)
# in every market above, so it's injected first instead.

FALLBACK_PLATFORMS = {
    "jp": [
        ("Rakuten Ichiba", "https://search.rakuten.co.jp/search/mall/{q}/"),
        ("Yodobashi Camera", "https://www.yodobashi.com/?word={q}"),
        ("Biccamera", "https://www.biccamera.com/bc/result/?q={q}"),
        ("Kakaku.com (price comparison)", "https://kakaku.com/search_results/{q}/"),
    ],
    "in": [
        ("Flipkart", "https://www.flipkart.com/search?q={q}"),
        ("Croma", "https://www.croma.com/searchB?q={q}%3Arelevance"),
        ("Reliance Digital", "https://www.reliancedigital.in/search?q={q}"),
        ("Tata Cliq", "https://www.tatacliq.com/search/?searchCategory=all&text={q}"),
    ],
    "au": [
        ("JB Hi-Fi", "https://www.jbhifi.com.au/search?q={q}"),
        ("Officeworks", "https://www.officeworks.com.au/shop/officeworks/search?q={q}"),
        ("Harvey Norman", "https://www.harveynorman.com.au/catalogsearch/result/?q={q}"),
        ("The Good Guys", "https://www.thegoodguys.com.au/search?q={q}"),
    ],
    "sg": [
        ("Shopee Singapore", "https://shopee.sg/search?keyword={q}"),
        ("Lazada Singapore", "https://www.lazada.sg/catalog/?q={q}"),
        ("Challenger", "https://www.challenger.sg/catalogsearch/result/?q={q}"),
        ("Courts", "https://www.courts.com.sg/catalogsearch/result/?q={q}"),
    ],
    "tw": [
        ("momo購物網", "https://www.momoshop.com.tw/search/searchShop.jsp?keyword={q}"),
        ("全國電子", "https://www.allstar.com.tw/searchProduct/{q}"),
    ],
}

SERPAPI_LOCALE = {
    "jp": ("google.co.jp", "jp", "ja"),
    "in": ("google.co.in", "in", "en"),
    "au": ("google.com.au", "au", "en"),
    "sg": ("google.com.sg", "sg", "en"),
    "tw": ("google.com.tw", "tw", "zh-tw"),
}


def _scrape_product(market_code: str, name: str, expected_price: float | None) -> dict:
    entry = {"product": name, "offers": []}

    amazon = scrape_amazon(name, market_code, expected_price)
    if amazon:
        entry["offers"].append(amazon)

    if market_code == "tw":
        pchome = scrape_pchome_tw(name, expected_price)
        if pchome:
            entry["offers"].append(pchome)
        yahoo = scrape_yahoo_tw(name, expected_price)
        if yahoo:
            entry["offers"].append(yahoo)

    if len(entry["offers"]) < MAX_PLATFORMS_PER_MARKET:
        domain, gl, hl = SERPAPI_LOCALE[market_code]
        serp = _serpapi_price(name, domain, gl, hl)
        if serp and serp["platform"] not in {o["platform"] for o in entry["offers"]}:
            entry["offers"].append(serp)

    for platform, template in FALLBACK_PLATFORMS.get(market_code, []):
        if len(entry["offers"]) >= MAX_PLATFORMS_PER_MARKET:
            break
        if any(o["platform"] == platform for o in entry["offers"]):
            continue
        entry["offers"].append(
            {
                "platform": platform,
                "url": template.format(q=urllib.parse.quote(name)),
                "method": "search_link",
            }
        )

    return entry


def scrape_market(market_code: str, google_store_products: list[dict], max_workers: int = 4) -> list[dict]:
    names = _product_names(google_store_products)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(
            pool.map(lambda pair: _scrape_product(market_code, pair[0], pair[1]), names)
        )


if __name__ == "__main__":
    import sys

    market = sys.argv[1] if len(sys.argv) > 1 else "au"
    query = sys.argv[2] if len(sys.argv) > 2 else "Pixel 11"
    print(json.dumps(scrape_amazon(query, market), indent=2, ensure_ascii=False))
