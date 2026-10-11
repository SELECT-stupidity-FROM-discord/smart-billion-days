import json
import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import classifier
import scraper

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_QUERY = 80
MAX_PAGE = 20
MAX_PRICE = 10_000_000
CACHE_TTL = 600

MODE_TTL = 15

_mode = {"live": True, "checked": float("-inf")}


def went_down(reason: str):
    if _mode["live"]:
        print(f"LIVE -> MAINTENANCE: {reason}")
    _mode.update(live=False, checked=time.monotonic())


def probe() -> bool:
    now = time.monotonic()
    if now - _mode["checked"] >= MODE_TTL:
        up = classifier.jev_up()
        if up != _mode["live"]:
            print("MAINTENANCE -> LIVE" if up else "LIVE -> MAINTENANCE: probe failed")
        _mode.update(live=up, checked=now)
    return _mode["live"]


_cache = {}
_lock = threading.Lock()


def cached_search(query: str, page: int = 1, price=None, sort=None, rating=None, discount=None):
    text, low, high = scraper.split_price(query)
    if price is not None:
        low, high = price
    opts = {"low": low, "high": high, "sort": sort, "rating": rating, "discount": discount}
    key = (query.lower(), page, *opts.values())
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1], True
    if classifier.is_spam(query.lower()):
        return {"query": query, "page": page, "listings": [], "errors": {}, "criteria": None,
                "filtered": [], "left_out": 0, "rejected": True}, True
    result = scraper.search(text, page, **opts)
    m = classifier.match(text, result["listings"])
    result.update(query=query, searched=text, listings=m["listings"], filtered=m["filtered"], criteria=m["criteria"],
                  left_out=m["left_out"], price={"low": low, "high": high},
                  sort=sort or "relevance", rating=rating, discount=discount)
    ok = not result["errors"] and not m["error"]
    if ok:
        with _lock:
            _cache[key] = (now, result)
    return result, ok


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/search":
            return self.api_search(url.query)
        if url.path == "/api/price":
            text, low, high = scraper.split_price(" ".join(parse_qs(url.query).get("q", [""])[0].split())[:MAX_QUERY])
            return self.send_json(200, {"query": text, "low": low, "high": high}, cache=86400)
        if url.path == "/api/status":
            return self.send_json(200, {"mode": "LIVE" if probe() else "MAINTENANCE"}, cache=MODE_TTL)
        return super().do_GET()

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def api_search(self, qs: str):
        params = parse_qs(qs, keep_blank_values=True)
        query = " ".join(params.get("q", [""])[0].split())
        page = params.get("page", ["1"])[0]
        if not query:
            return self.send_json(400, {"error": "Missing q"})
        if len(query) > MAX_QUERY:
            return self.send_json(400, {"error": f"q is longer than {MAX_QUERY} characters"})
        if not page.isdecimal() or not 1 <= int(page) <= MAX_PAGE:
            return self.send_json(400, {"error": f"page must be 1 to {MAX_PAGE}"})
        price = None
        if "min" in params or "max" in params:
            ends = [params.get(k, [""])[0].strip() for k in ("min", "max")]
            if any(e and not (e.isdecimal() and int(e) <= MAX_PRICE) for e in ends):
                return self.send_json(400, {"error": f"min and max must be whole rupees up to {MAX_PRICE}"})
            low, high = (int(e) if e and int(e) else None for e in ends)
            price = (high, low) if low and high and low > high else (low, high)
        sort, rating, discount = (params.get(k, [""])[0] for k in ("sort", "rating", "discount"))
        if sort and sort not in scraper.SORTS:
            return self.send_json(400, {"error": f"sort must be one of {', '.join(scraper.SORTS)}"})
        if rating and rating not in [str(r) for r in scraper.RATINGS]:
            return self.send_json(400, {"error": "rating must be 3 or 4"})
        if discount and discount not in [str(d) for d in scraper.DISCOUNTS]:
            return self.send_json(400, {"error": "discount must be 10, 30, 50 or 70"})
        options = {"sort": None if sort in ("", "relevance") else sort,
                   "rating": int(rating) if rating else None, "discount": int(discount) if discount else None}
        if self.headers.get("x-vercel-oidc-token"):
            os.environ["VERCEL_OIDC_TOKEN"] = self.headers["x-vercel-oidc-token"]
        if not _mode["live"] and not probe():
            return self.send_json(503, {"mode": "MAINTENANCE"})
        try:
            result, ok = cached_search(query, int(page), price, **options)
            return self.send_json(200, result, cache=CACHE_TTL if ok else 0)
        except classifier.JevDown as e:
            went_down(str(e))
            return self.send_json(503, {"mode": "MAINTENANCE"})
        except Exception as e:
            self.log_error("search failed for %r: %r", query, e)
            return self.send_json(500, {"error": "Search failed"})

    def send_json(self, code: int, payload: dict, cache: int = 0):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", f"public, s-maxage={cache}" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)


def make_server(host="127.0.0.1", port=8000):
    return ThreadingHTTPServer((host, port), partial(Handler, directory=str(WEB_DIR)))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Serving http://127.0.0.1:{port}")
    make_server(port=port).serve_forever()
