import json
import re
import atexit
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

TIMEOUT = 12
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IN,en;q=0.9",
}


@dataclass
class Listing:
    store: str
    id: str
    title: str
    brand: Optional[str]
    price: int
    mrp: Optional[int]
    discount_pct: Optional[int]
    image: Optional[str]
    url: str
    sponsored: bool
    category: Optional[str]
    rating: Optional[float] = None
    ratings: Optional[int] = None
    variant: Optional[str] = None


class StoreUnavailable(Exception):
    pass


def fetch(url: str, headers: Optional[dict] = None, timeout: int = TIMEOUT) -> str:
    try:
        with urlopen(Request(url, headers=headers or HEADERS), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except HTTPError as e:
        raise StoreUnavailable(f"HTTP {e.code}") from e
    except (URLError, TimeoutError) as e:
        raise StoreUnavailable(str(getattr(e, "reason", e))) from e


_driver = None
_driver_lock = threading.Lock()
BROWSER_WAIT = 15


def _new_driver():
    from selenium import webdriver
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.page_load_strategy = "eager"
    return webdriver.Chrome(options=opts)


@atexit.register
def _quit_driver():
    if _driver:
        _driver.quit()


def fetch_browser(url: str) -> str:
    global _driver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.support.ui import WebDriverWait
    ready = ('div[data-component-type="s-search-result"]', "No results for")
    with _driver_lock:
        for attempt in (1, 2):
            try:
                _driver = _driver or _new_driver()
                _driver.get(url)
                try:
                    WebDriverWait(_driver, BROWSER_WAIT).until(
                        lambda d: d.find_elements("css selector", ready[0]) or ready[1] in d.page_source)
                except TimeoutException:
                    pass
                return _driver.page_source
            except WebDriverException as e:
                try:
                    _driver and _driver.quit()
                except WebDriverException:
                    pass
                _driver = None
                if attempt == 2:
                    raise StoreUnavailable(f"browser: {e.msg or type(e).__name__}") from e


def rupees(text: Optional[str]) -> Optional[int]:
    m = re.search(r"[\d,]+(?:\.\d+)?", text or "")
    return round(float(m.group().replace(",", ""))) if m else None


def discount(price: int, mrp: Optional[int]) -> Optional[int]:
    return round((mrp - price) * 100 / mrp) if mrp and mrp > price else None


def parse_amazon(html: str) -> list:
    if "validateCaptcha" in html or "Type the characters you see" in html:
        raise StoreUnavailable("captcha")
    if "bm-verify" in html or "/_sec/verify" in html:
        raise StoreUnavailable("bot check")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for div in soup.select('div[data-component-type="s-search-result"]'):
        asin = div.get("data-asin")
        h2s = div.select("h2")
        price = rupees(t.get_text() if (t := div.select_one(".a-price:not(.a-text-price) .a-offscreen")) else None)
        if not asin or not h2s or not price:
            continue
        mrp = rupees(t.get_text() if (t := div.select_one('.a-price[data-a-strike="true"] .a-offscreen')) else None)
        img = div.select_one("img.s-image")
        stars = re.search(r"([0-9.]+) out of 5 stars", t.get_text()) if (t := div.select_one(".a-icon-alt")) else None
        count = re.search(r"([\d,]+) ratings?$", t["aria-label"]) if (t := div.select_one('a[aria-label$="rating"], a[aria-label$="ratings"]')) else None
        out.append(Listing(
            store="amazon",
            id=asin,
            title=h2s[-1].get_text(" ", strip=True),
            brand=h2s[0].get_text(" ", strip=True) if len(h2s) > 1 else None,
            price=price,
            mrp=mrp,
            discount_pct=discount(price, mrp),
            image=img.get("src") if img else None,
            url=f"https://www.amazon.in/dp/{asin}",
            sponsored="AdHolder" in div.get("class", []) or bool(div.select_one(".puis-sponsored-label-text")),
            category=None,
            rating=float(stars.group(1)) if stars else None,
            ratings=int(count.group(1).replace(",", "")) if count else None,
        ))
    if not out and "s-search-result" not in html and "No results for" not in html:
        raise StoreUnavailable("unrecognised page")
    return out


def parse_flipkart(html: str) -> list:
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if not m:
        raise StoreUnavailable("captcha" if "recaptcha" in html.lower() else "unrecognised page")
    products = []

    def walk(o):
        if isinstance(o, dict):
            w = o.get("widget")
            if isinstance(w, dict) and w.get("type") == "PRODUCT_SUMMARY":
                products.extend(w.get("data", {}).get("products", []))
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))

    out, seen = [], set()
    for p in products:
        info = p.get("productInfo", {})
        v = info.get("value") or {}
        pid = v.get("id")
        if not pid or pid in seen:
            continue
        pr = v.get("pricing") or {}
        price = next((x["value"] for x in pr.get("prices") or [] if not x.get("strikeOff")), None) \
            or (pr.get("finalPrice") or {}).get("value")
        mrp = next((x["value"] for x in pr.get("prices") or [] if x.get("strikeOff")), None) \
            or (pr.get("mrp") or {}).get("value")
        if not price:
            continue
        seen.add(pid)
        t = v.get("titles") or {}
        images = (v.get("media") or {}).get("images") or []
        img = images[0].get("url") if images else None
        if img:
            img = (img.replace("{@width}", "416").replace("{@height}", "416")
                      .replace("{@quality}", "70").replace("http://", "https://", 1))
        path = urlsplit((info.get("action") or {}).get("url") or "")
        out.append(Listing(
            store="flipkart",
            id=pid,
            title=t.get("newTitle") or t.get("title") or "",
            brand=t.get("superTitle"),
            price=int(price),
            mrp=int(mrp) if mrp else None,
            discount_pct=discount(int(price), int(mrp) if mrp else None),
            image=img,
            url=f"https://www.flipkart.com{path.path}?pid={pid}",
            sponsored=False,
            category=(v.get("analyticsData") or {}).get("vertical"),
            variant=t.get("subtitle"),
            rating=(v.get("rating") or {}).get("average") or None,
            ratings=(v.get("rating") or {}).get("count") or None,
        ))
    return out


def fetch_amazon(url: str) -> str:
    base = os.environ.get("AMAZON_URL")
    if not base:
        return fetch_browser(url)
    return fetch(f"{base.rstrip('/')}/?{urlsplit(url).query}", timeout=30)


_NUM = (r"(?:₹|\brs\.?|\binr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?)?(?:\s*(?:rs\.?|rupees|inr|/-))?"
        r"(?![\w.]|\s*(?:m|cm|mm|kg|g|l|ltr|litres?|liters?|ml|w|watts?|mah|inch(?:es)?|in|gb|tb|hz|hrs?|hours?|years?|"
        r"metres?|meters?|ft|feet|pcs|pieces|pack)\b)")
_PRICE_RULES = [
    (rf"\b(?:between|from)\s+{_NUM}\s+(?:and|to|-)\s+{_NUM}", "both"),
    (rf"(?<![\w.]){_NUM}\s*(?:-|to)\s*{_NUM}", "both?"),
    (rf"\b(?:under|below|less than|within|upto|up to|max(?:imum)?|budget(?: of)?|not more than)\s*{_NUM}", "high"),
    (rf"\b(?:above|over|more than|min(?:imum)?|starting(?: at| from)?|from|at least)\s*{_NUM}", "low"),
    (rf"(?<![\w.]){_NUM}\s*\+(?!\w)", "low"),
]


def _rupees(num: str, unit: str) -> int:
    n = float(num.replace(",", ""))
    unit = (unit or "").lower()
    return int(n * (1000 if unit in ("k", "thousand") else 100000 if unit.startswith("la") else 1))


def split_price(query: str):
    for pattern, ends in _PRICE_RULES:
        m = re.search(pattern, query, re.I)
        if not m:
            continue
        if ends == "both?" and not re.search(r"₹|rs|inr|k\b|thousand|lakh|lac", m.group(0), re.I):
            continue
        values = [_rupees(m.group(i), m.group(i + 1)) for i in range(1, m.lastindex + 1, 2) if m.group(i)]
        low, high = (sorted(values) if ends.startswith("both") else
                     (None, values[0]) if ends == "high" else (values[0], None))
        rest = " ".join((query[:m.start()] + " " + query[m.end():]).split())
        rest = re.sub(r"\b(?:price|priced|budget|range|for|rs|rupees)\s*$", "", rest, flags=re.I).strip()
        return (rest or query), low, high
    return query, None, None


SORTS = {
    "relevance": (None, None),
    "price-asc": ("price-asc-rank", "price_asc"),
    "price-desc": ("price-desc-rank", "price_desc"),
    "rating": ("review-rank", "popularity"),
    "popular": ("exact-aware-popularity-rank", "popularity"),
    "newest": ("date-desc-rank", "recency_desc"),
}
RATINGS = {4: "1318476031", 3: "1318477031"}
DISCOUNTS = (10, 30, 50, 70)


def amazon_url(q: str, page: int, low=None, high=None, sort=None, rating=None, discount=None) -> str:
    qs = {"k": q, "page": page}
    if low or high:
        qs.update({"low-price": low or "", "high-price": high or ""})
    if SORTS.get(sort, (None,))[0]:
        qs["s"] = SORTS[sort][0]
    if rating:
        qs["rh"] = f"p_72:{RATINGS[rating]}"
    if discount:
        qs["pct-off"] = f"{discount}-"
    return "https://www.amazon.in/s?" + urlencode(qs) + "&dc"


def flipkart_url(q: str, page: int, low=None, high=None, sort=None, rating=None, discount=None) -> str:
    qs = [("q", q), ("page", page)]
    if SORTS.get(sort, (None, None))[1]:
        qs.append(("sort", SORTS[sort][1]))
    facets = []
    if low or high:
        facets += [f"facets.price_range.from={low or 'Min'}", f"facets.price_range.to={high or 'Max'}"]
    if rating:
        facets.append(f"facets.rating[]={rating}★ & above")
    if discount:
        facets.append(f"facets.discount_range_v1[]={discount}% or more")
    return "https://www.flipkart.com/search?" + urlencode(qs + [("p[]", f) for f in facets])


STORES = {
    "amazon": (amazon_url, fetch_amazon, parse_amazon),
    "flipkart": (flipkart_url, fetch, parse_flipkart),
}


def keep(x: Listing, low=None, high=None, rating=None, discount=None, **_) -> bool:
    return ((not low or x.price >= low) and (not high or x.price <= high)
            and (not rating or (x.rating or 0) >= rating) and (not discount or (x.discount_pct or 0) >= discount))


def search_store(store: str, query: str, page: int = 1, **opts) -> list:
    url, get, parse = STORES[store]
    return [x for x in parse(get(url(query, page, **opts))) if keep(x, **opts)]


def search(query: str, page: int = 1, **opts) -> dict:
    listings, errors = [], {}
    with ThreadPoolExecutor(len(STORES)) as pool:
        futures = {s: pool.submit(search_store, s, query, page, **opts) for s in STORES}
        for store, f in futures.items():
            try:
                listings += [x for x in f.result() if not x.sponsored]
            except StoreUnavailable as e:
                errors[store] = str(e)
    order = {"price-asc": lambda x: x.price, "price-desc": lambda x: -x.price, "rating": lambda x: -(x.rating or 0)}
    if opts.get("sort") in order:
        listings.sort(key=order[opts["sort"]])
    return {"query": query, "page": page, "listings": [asdict(x) for x in listings], "errors": errors}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: python scraper.py "wired earphones" [page]')
    page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    print(json.dumps(search(sys.argv[1], page), ensure_ascii=False, indent=2))
