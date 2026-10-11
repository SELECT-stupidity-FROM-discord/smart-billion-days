import json
import mimetypes
import os
import threading
import time
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIServer, make_server as wsgi_server

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


STATUS = {200: "200 OK", 400: "400 Bad Request", 404: "404 Not Found", 500: "500 Internal Server Error",
          503: "503 Service Unavailable"}


def api_search(qs: str):
    params = parse_qs(qs, keep_blank_values=True)
    query = " ".join(params.get("q", [""])[0].split())
    page = params.get("page", ["1"])[0]
    if not query:
        return 400, {"error": "Missing q"}, 0
    if len(query) > MAX_QUERY:
        return 400, {"error": f"q is longer than {MAX_QUERY} characters"}, 0
    if not page.isdecimal() or not 1 <= int(page) <= MAX_PAGE:
        return 400, {"error": f"page must be 1 to {MAX_PAGE}"}, 0
    price = None
    if "min" in params or "max" in params:
        ends = [params.get(k, [""])[0].strip() for k in ("min", "max")]
        if any(e and not (e.isdecimal() and int(e) <= MAX_PRICE) for e in ends):
            return 400, {"error": f"min and max must be whole rupees up to {MAX_PRICE}"}, 0
        low, high = (int(e) if e and int(e) else None for e in ends)
        price = (high, low) if low and high and low > high else (low, high)
    sort, rating, discount = (params.get(k, [""])[0] for k in ("sort", "rating", "discount"))
    if sort and sort not in scraper.SORTS:
        return 400, {"error": f"sort must be one of {', '.join(scraper.SORTS)}"}, 0
    if rating and rating not in [str(r) for r in scraper.RATINGS]:
        return 400, {"error": "rating must be 3 or 4"}, 0
    if discount and discount not in [str(d) for d in scraper.DISCOUNTS]:
        return 400, {"error": "discount must be 10, 30, 50 or 70"}, 0
    options = {"sort": None if sort in ("", "relevance") else sort,
               "rating": int(rating) if rating else None, "discount": int(discount) if discount else None}
    if not _mode["live"] and not probe():
        return 503, {"mode": "MAINTENANCE"}, 0
    try:
        result, ok = cached_search(query, int(page), price, **options)
        return 200, result, CACHE_TTL if ok else 0
    except classifier.JevDown as e:
        went_down(str(e))
        return 503, {"mode": "MAINTENANCE"}, 0
    except Exception as e:
        print(f"search failed for {query!r}: {e!r}")
        return 500, {"error": "Search failed"}, 0


def static(path: str, start_response):
    file = (WEB_DIR / path.lstrip("/")).resolve()
    if file.is_dir():
        file = file / "index.html"
    if WEB_DIR not in file.parents or not file.is_file():
        start_response(STATUS[404], [("Content-Type", "text/plain")])
        return [b"Not found"]
    data = file.read_bytes()
    start_response(STATUS[200], [("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream"),
                                 ("Content-Length", str(len(data))), ("Cache-Control", "no-cache")])
    return [data]


def app(environ, start_response):
    path, qs = environ.get("PATH_INFO") or "/", environ.get("QUERY_STRING", "")
    if environ.get("HTTP_X_VERCEL_OIDC_TOKEN"):
        os.environ["VERCEL_OIDC_TOKEN"] = environ["HTTP_X_VERCEL_OIDC_TOKEN"]
    if path == "/api/search":
        code, payload, cache = api_search(qs)
    elif path == "/api/price":
        text, low, high = scraper.split_price(" ".join(parse_qs(qs).get("q", [""])[0].split())[:MAX_QUERY])
        code, payload, cache = 200, {"query": text, "low": low, "high": high}, 86400
    elif path == "/api/status":
        code, payload, cache = 200, {"mode": "LIVE" if probe() else "MAINTENANCE"}, MODE_TTL
    elif path.startswith("/api/"):
        code, payload, cache = 404, {"error": "Not found"}, 0
    else:
        return static(path, start_response)
    data = json.dumps(payload, ensure_ascii=False).encode()
    start_response(STATUS[code], [("Content-Type", "application/json; charset=utf-8"),
                                  ("Content-Length", str(len(data))),
                                  ("Cache-Control", f"public, s-maxage={cache}" if cache else "no-store")])
    return [data]


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def make_server(host="127.0.0.1", port=8000):
    return wsgi_server(host, port, app, server_class=ThreadingWSGIServer)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Serving http://127.0.0.1:{port}")
    make_server(port=port).serve_forever()
