"""Scrapes store.google.com for catalog + pricing, per market.

Google Store pages are server-rendered with schema.org Product/AggregateOffer
microdata embedded directly in the HTML, so a plain HTTP GET + regex parse is
enough -- no headless browser required. This keeps the daily GitHub Actions
job fast and dependency-free (stdlib only).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape

from markets import CATEGORY_SEED_PATHS, CONFIG_SLUGS, EXTRA_PRODUCT_SLUGS, MARKETS

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

SLUG_RE = re.compile(r"/(?:[a-z]{2})/(?:config|product)/([a-zA-Z0-9_]+)\?")

PRODUCT_BLOCK_RE = re.compile(
    r'<div itemtype="https://schema\.org/Product" itemscope>(.*?)'
    r'(?=<div itemtype="https://schema\.org/Product" itemscope>|</c-wiz>|$)',
    re.S,
)
NAME_RE = re.compile(r'<meta itemprop="name" content="([^"]*)"')
OFFER_BLOCK_RE = re.compile(
    r'<div itemprop="offers" itemscope itemtype="https://schema\.org/AggregateOffer">(.*?)</div>',
    re.S,
)
OFFER_DESC_RE = re.compile(r'<meta itemprop="description" content="([^"]*)"')
OFFER_PRICE_RE = re.compile(r'<meta itemprop="lowPrice" content="([^"]*)"')
OFFER_CURRENCY_RE = re.compile(r'<meta itemprop="priceCurrency" content="([^"]*)"')
OFFER_AVAIL_RE = re.compile(r'schema\.org/(InStock|OutOfStock|PreOrder|SoldOut)')

SINGLE_NAME_RE = re.compile(r'<meta itemprop="name" content="([^"]*)"/?>')
SINGLE_PRICE_RE = re.compile(
    r'itemprop="offers"[^>]*itemtype="https://schema\.org/AggregateOffer">'
    r'<meta itemprop="lowPrice" content="([^"]*)"/?>'
    r'<meta itemprop="priceCurrency" content="([^"]*)"/?>'
)
DOCID_RE = re.compile(r'data-product-docid="([^"]*)"')


def fetch(url: str, retries: int = 1, timeout: int = 15) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries:
                return None
        except Exception:
            # Covers URLError, socket.timeout, ssl errors, connection resets --
            # a single flaky fetch should never take down the whole run.
            if attempt == retries:
                return None
    return None


def discover_slugs(market_code: str) -> set[str]:
    cfg = MARKETS[market_code]
    slugs = set(EXTRA_PRODUCT_SLUGS)
    for path in CATEGORY_SEED_PATHS:
        url = f"https://store.google.com/{cfg['store_path']}/{path}?hl={cfg['store_hl']}"
        html = fetch(url)
        if not html:
            continue
        slugs.update(SLUG_RE.findall(html))
    return slugs


def parse_config_page(html: str) -> list[dict]:
    """Phone configurator pages: one Product block, many colour/storage offers."""
    products = []
    for block in PRODUCT_BLOCK_RE.findall(html):
        name_m = NAME_RE.search(block)
        if not name_m:
            continue
        name = unescape(name_m.group(1))
        variants = []
        for offer in OFFER_BLOCK_RE.findall(block):
            desc_m = OFFER_DESC_RE.search(offer)
            price_m = OFFER_PRICE_RE.search(offer)
            currency_m = OFFER_CURRENCY_RE.search(offer)
            avail_m = OFFER_AVAIL_RE.search(offer)
            if not (price_m and currency_m):
                continue
            variants.append(
                {
                    "variant": unescape(desc_m.group(1)) if desc_m else None,
                    "price": float(price_m.group(1)),
                    "currency": currency_m.group(1),
                    "availability": avail_m.group(1) if avail_m else "Unknown",
                }
            )
        if variants:
            products.append({"name": name, "variants": variants})
    return products


def parse_product_page(html: str) -> dict | None:
    """Accessory/watch/home pages: single aggregate 'from' price + SKU docids."""
    name_m = SINGLE_NAME_RE.search(html)
    price_m = SINGLE_PRICE_RE.search(html)
    if not name_m or not price_m:
        return None
    docids = sorted(set(DOCID_RE.findall(html)))
    return {
        "name": unescape(name_m.group(1)),
        "from_price": float(price_m.group(1)),
        "currency": price_m.group(2),
        "sku_docids": docids,
    }


def _fetch_slug(cfg: dict, slug: str) -> tuple[str, str | None]:
    primary = "config" if slug in CONFIG_SLUGS else "product"
    url = f"https://store.google.com/{cfg['store_path']}/{primary}/{slug}?hl={cfg['store_hl']}"
    html = fetch(url)
    if not html:
        other = "product" if primary == "config" else "config"
        url = f"https://store.google.com/{cfg['store_path']}/{other}/{slug}?hl={cfg['store_hl']}"
        html = fetch(url)
    return slug, html


def scrape_market(market_code: str, max_workers: int = 8) -> dict:
    cfg = MARKETS[market_code]
    slugs = sorted(discover_slugs(market_code))
    products = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for slug, html in pool.map(lambda s: _fetch_slug(cfg, s), slugs):
            if not html:
                continue

            parsed = parse_config_page(html)
            if parsed:
                for p in parsed:
                    products.append({"slug": slug, "type": "config", **p})
                continue

            single = parse_product_page(html)
            if single:
                products.append({"slug": slug, "type": "product", **single})

    return {
        "market": market_code,
        "market_name": cfg["name"],
        "currency": cfg["currency"],
        "source": "store.google.com",
        "products": _dedupe(products),
    }


def _signature(product: dict) -> tuple:
    if product["type"] == "config":
        prices = tuple(sorted(v["price"] for v in product["variants"]))
    else:
        prices = (product.get("from_price"),)
    return (product["name"], prices)


def _dedupe(products: list[dict]) -> list[dict]:
    """Several legacy slugs (e.g. pixel_watch_3) now redirect to the current
    product page. Keep only the first slug seen for each identical
    name+price signature."""
    seen = set()
    result = []
    for p in products:
        sig = _signature(p)
        if sig in seen:
            continue
        seen.add(sig)
        result.append(p)
    return result


if __name__ == "__main__":
    import sys

    code = sys.argv[1] if len(sys.argv) > 1 else "au"
    result = scrape_market(code)
    print(json.dumps(result, indent=2, ensure_ascii=False))
