import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import scraper
import server

calls = []


def fake_search(q, page=1, low=None, high=None, sort=None, rating=None, discount=None):
    if sort or rating or discount:
        calls.append((q, sort, rating, discount))
        return {"query": q, "page": page, "listings": [{"store": "flipkart", "title": q}], "errors": {}}
    calls.append(q if page == 1 and not (low or high) else (q, page, low, high) if (low or high) else (q, page))
    errors = {"amazon": "bot check"} if "down" in q else {}
    return {"query": q, "page": page, "listings": [{"store": "flipkart", "title": q}], "errors": errors}


def get(base, path):
    try:
        with urlopen(base + path) as r:
            return r.status, r.read()
    except HTTPError as e:
        return e.code, e.read()


def test():
    scraper.search = fake_search
    srv = server.make_server(port=0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert get(base, "/api/search")[0] == 400
        assert get(base, "/api/search?q=%20%20")[0] == 400
        assert get(base, "/api/search?q=" + "x" * 81)[0] == 400

        code, body = get(base, "/api/search?q=Wired%20%20earphones")
        assert code == 200 and json.loads(body)["query"] == "Wired earphones"
        get(base, "/api/search?q=wired+earphones")
        assert calls == ["Wired earphones"], calls

        assert get(base, "/api/search?q=a&page=0")[0] == 400
        assert get(base, "/api/search?q=a&page=21")[0] == 400
        assert get(base, "/api/search?q=a&page=x")[0] == 400
        code, body = get(base, "/api/search?q=wired+earphones&page=2")
        assert code == 200 and json.loads(body)["page"] == 2
        assert calls[-1] == ("wired earphones", 2), calls

        code, body = get(base, "/api/search?q=iem+above+5000")
        assert calls[-1] == ("iem", 1, 5000, None) and json.loads(body)["price"] == {"low": 5000, "high": None}, calls
        get(base, "/api/search?q=iem+above+5000&min=7000&max=")
        assert calls[-1] == ("iem", 1, 7000, None), calls
        get(base, "/api/search?q=iem&min=9000&max=2000")
        assert calls[-1] == ("iem", 1, 2000, 9000), calls
        assert get(base, "/api/search?q=iem&min=-5")[0] == 400
        get(base, "/api/search?q=iem+above+5000&min=&max=")
        assert calls[-1] == "iem", calls
        assert json.loads(get(base, "/api/price?q=tv%2015k-20k")[1]) == {"query": "tv", "low": 15000, "high": 20000}

        code, body = get(base, "/api/search?q=iem&sort=price-asc&rating=4&discount=30")
        assert calls[-1] == ("iem", "price-asc", 4, 30) and json.loads(body)["sort"] == "price-asc", calls
        get(base, "/api/search?q=iem&sort=rating")
        assert calls[-1] == ("iem", "rating", None, None), calls
        get(base, "/api/search?q=iem&sort=relevance")
        assert calls[-1] != ("iem", "relevance", None, None)
        for bad in ("sort=cheapest", "rating=5", "discount=25"):
            assert get(base, "/api/search?q=iem&" + bad)[0] == 400, bad

        get(base, "/api/search?q=down")
        get(base, "/api/search?q=down")
        assert calls.count("down") == 2, calls

        import classifier
        server.MODE_TTL = 0

        def jev_fails(q):
            raise classifier.JevDown("test outage")
        classifier.is_spam, classifier.jev_up = jev_fails, lambda: False
        code, body = get(base, "/api/search?q=jev+test")
        assert code == 503 and json.loads(body)["mode"] == "MAINTENANCE", (code, body)
        assert json.loads(get(base, "/api/status")[1])["mode"] == "MAINTENANCE"
        assert get(base, "/api/search?q=anything")[0] == 503
        classifier.is_spam, classifier.jev_up = lambda q: False, lambda: True
        assert json.loads(get(base, "/api/status")[1])["mode"] == "LIVE"
        assert get(base, "/api/search?q=anything")[0] == 200

        code, body = get(base, "/")
        assert code == 200 and b"Search Amazon and Flipkart" in body
    finally:
        srv.shutdown()
    print("ok")


if __name__ == "__main__":
    test()
