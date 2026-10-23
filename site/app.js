const DATA_URL = "../data/latest.json";

const MARKET_ORDER = ["jp", "in", "au", "sg", "tw"];
const MARKET_FLAG = { jp: "🇯🇵", in: "🇮🇳", au: "🇦🇺", sg: "🇸🇬", tw: "🇹🇼" };

const CATEGORY_RULES = [
  { key: "phones", label: "Phones", test: (slug) => /^pixel_(9|10|11)/.test(slug) },
  { key: "watches", label: "Watches & Trackers", test: (slug) => /watch|fitbit/.test(slug) },
  { key: "earbuds", label: "Earbuds", test: (slug) => /buds/.test(slug) },
  { key: "home", label: "Home & Other", test: () => true },
];

let STATE = { data: null, market: "au", category: "phones" };

async function main() {
  const res = await fetch(DATA_URL, { cache: "no-store" });
  STATE.data = await res.json();
  renderMarketNav();
  renderShell();
}

function categorize(slug) {
  return CATEGORY_RULES.find((r) => r.test(slug)).key;
}

function renderMarketNav() {
  const nav = document.getElementById("market-nav");
  nav.innerHTML = "";
  for (const code of MARKET_ORDER) {
    const m = STATE.data.markets[code];
    if (!m) continue;
    const btn = document.createElement("button");
    btn.className = "market-tab" + (code === STATE.market ? " active" : "");
    btn.textContent = `${MARKET_FLAG[code] || ""} ${m.market_name}`;
    btn.onclick = () => {
      STATE.market = code;
      renderMarketNav();
      renderShell();
    };
    nav.appendChild(btn);
  }
}

function renderShell() {
  const main = document.getElementById("app-main");
  main.innerHTML = "";

  const catNav = document.createElement("div");
  catNav.className = "category-nav";
  for (const cat of CATEGORY_RULES) {
    const btn = document.createElement("button");
    btn.className = "category-tab" + (cat.key === STATE.category ? " active" : "");
    btn.textContent = cat.label;
    btn.onclick = () => {
      STATE.category = cat.key;
      renderShell();
    };
    catNav.appendChild(btn);
  }
  main.appendChild(catNav);

  const grid = document.createElement("div");
  grid.className = "grid";
  main.appendChild(grid);

  const marketData = STATE.data.markets[STATE.market];
  const products = marketData.google_store.filter(
    (p) => categorize(p.slug) === STATE.category
  );

  if (products.length === 0) {
    grid.innerHTML = `<div class="empty-state">No products found in this category for ${marketData.market_name}.</div>`;
    return;
  }

  const competitorIndex = buildCompetitorIndex(marketData.competitors);

  for (const product of products) {
    grid.appendChild(renderCard(product, marketData.currency, competitorIndex, STATE.market));
  }

  document.getElementById("generated-at").textContent = new Date(
    STATE.data.generated_at
  ).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function cleanName(name) {
  return name
    .replace(/^(Buy|Discover the [Nn]ew|Explore|認識全新|購買搭載[^的]*的|立即購買)\s*/i, "")
    .split(" with ")[0]
    .split(" in ")[0]
    .trim()
    .replace(/^Google\s+/, "")
    .trim();
}

function buildCompetitorIndex(competitors) {
  const idx = new Map();
  for (const entry of competitors) {
    idx.set(entry.product, entry.offers);
  }
  return idx;
}

function findOffers(index, productName) {
  const clean = cleanName(productName);
  if (index.has(clean)) return index.get(clean);
  for (const [key, offers] of index) {
    if (key.startsWith(clean) || clean.startsWith(key)) return offers;
  }
  return [];
}

function fmtMoney(value, currency) {
  if (value == null) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${currency} ${value}`;
  }
}

function availBadge(avail) {
  if (avail === "InStock") return `<span class="badge in-stock">In stock</span>`;
  if (avail === "OutOfStock") return `<span class="badge out-stock">Out of stock</span>`;
  if (avail === "PreOrder") return `<span class="badge preorder">Pre-order</span>`;
  return "";
}

function lowestGoogleStorePrice(product) {
  if (product.type === "config") {
    const inStock = product.variants.filter((v) => v.availability === "InStock");
    const pool = inStock.length ? inStock : product.variants;
    return Math.min(...pool.map((v) => v.price));
  }
  return product.from_price;
}

function renderCard(product, currency, competitorIndex, marketCode) {
  const card = document.createElement("div");
  card.className = "card";

  const title = document.createElement("h3");
  title.textContent = cleanName(product.name);
  card.appendChild(title);

  const gsPrice = lowestGoogleStorePrice(product);
  const priceEl = document.createElement("div");
  priceEl.className = "price-from";
  priceEl.innerHTML = `${fmtMoney(gsPrice, currency)} <span class="unit">from, Google Store</span>`;
  card.appendChild(priceEl);

  if (product.type === "config") {
    const table = document.createElement("table");
    table.className = "variant-table";
    table.innerHTML = `<thead><tr><th>Variant</th><th>Price</th><th>Stock</th></tr></thead>`;
    const tbody = document.createElement("tbody");
    for (const v of product.variants) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${v.variant || "—"}</td><td>${fmtMoney(v.price, v.currency)}</td><td>${availBadge(v.availability)}</td>`;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    card.appendChild(table);
  } else if (product.sku_docids && product.sku_docids.length) {
    const hint = document.createElement("div");
    hint.className = "footer-note";
    hint.style.marginTop = "6px";
    hint.textContent = `${product.sku_docids.length} SKU variant(s) (colour/size/connectivity) at this price.`;
    card.appendChild(hint);
  }

  const offers = findOffers(competitorIndex, product.name);
  if (offers && offers.length) {
    const box = document.createElement("div");
    box.className = "compare-box";
    box.innerHTML = `<div class="label">Compare elsewhere</div>`;
    for (const offer of offers) {
      const row = document.createElement("div");
      row.className = "offer-row";
      let priceHtml = "";
      if (offer.method === "scraped" || offer.method === "serpapi") {
        priceHtml = `<span class="offer-price">${fmtMoney(offer.price, currency)}</span>`;
        if (offer.price != null && gsPrice != null) {
          const diffPct = ((offer.price - gsPrice) / gsPrice) * 100;
          if (diffPct <= -2) {
            priceHtml += `<span class="discount-badge">${diffPct.toFixed(0)}%</span>`;
          } else if (diffPct >= 2) {
            priceHtml += `<span class="markup-badge">+${diffPct.toFixed(0)}%</span>`;
          }
        }
      } else {
        priceHtml = `<span class="offer-price" style="color:var(--muted); font-weight:400;">Check price →</span>`;
      }
      row.innerHTML = `<a href="${offer.url}" target="_blank" rel="noopener">${offer.platform}</a>${priceHtml}`;
      box.appendChild(row);
    }
    card.appendChild(box);
  }

  return card;
}

main().catch((err) => {
  document.getElementById("app-main").innerHTML = `<div class="empty-state">Couldn't load price data: ${err}. Have you run <code>python3 scraper/run.py</code> yet?</div>`;
});
