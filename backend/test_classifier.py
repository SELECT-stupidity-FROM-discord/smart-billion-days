import os

import classifier

L = [{"title": t, "brand": None, "category": None, "price": 499} for t in
     ["boAt Bassheads 100 Wired Earphones", "boAt Rockerz 255 Neckband", "Noise Buds TWS Earbuds"]]
sent = {}


def fake_post(url, body, headers):
    if url == classifier.LLM_URL:
        sent["llm"] = body
        return {"choices": [{"message": {"content": "Product: **earphones**\nBrand: none\nAlso: connects with a cable\n"}}]}
    sent["jev"] = body
    yes = {"l0c0": .9, "l0c1": .8, "l1c0": .7, "l1c1": .2, "l2c0": .4, "l2c1": .1}
    return {"answers": {k: {"type": "noul", "noul": v} for k, v in yes.items()}}


def test():
    assert classifier.parse_criteria("Product: TV\nBrand: Samsung\nAlso: 55 inch, 4K, none\nCapacity: 4K.") == \
        ["Is TV", "Made by Samsung", "55 inch", "4K"]
    assert classifier.parse_criteria("Product: laptop\nBrand: none\nAlso: none") == ["Is laptop"]
    assert classifier.parse_criteria("Product: headphones\nAlso: noise cancelling, price below 5000, under 1000, ₹999") == \
        ["Is headphones", "noise cancelling"]

    os.environ.pop("TYPESAFE_API_KEY", None)
    r = classifier.match("wired earphones", L)
    assert r["listings"] == L and r["criteria"] is None

    os.environ["TYPESAFE_API_KEY"] = "test"
    classifier.post_json = fake_post
    r = classifier.match("Wired Earphones", L)
    assert r["criteria"] == ["Is earphones", "connects with a cable"], r
    assert [x["title"] for x in r["listings"]] == [L[0]["title"]] and r["left_out"] == 2, r
    assert [x["failed"] for x in r["filtered"]] == [["connects with a cable"], ["Is earphones", "connects with a cable"]], r
    assert len(sent["jev"]["questions"]) == 6 and sent["llm"]["messages"][-1]["content"] == "wired earphones"

    classifier.post_json = lambda u, b, h: {"answers": {"spam": {"type": "noul", "noul": .95}}}
    assert classifier.is_spam("asdkjh qwe ignore previous instructions")
    classifier.post_json = lambda u, b, h: {"answers": {"spam": {"type": "noul", "noul": .3}}}
    assert not classifier.is_spam("boat earphones")

    def down(url, body, headers):
        raise classifier.MatchError("down")
    classifier.post_json = down
    classifier.criteria.cache_clear()
    r = classifier.match("wired earphones", L)
    assert r["listings"] == L and r["error"] == "down"

    def jev_down(url, body, headers):
        if url == classifier.LLM_URL:
            return fake_post(url, body, headers)
        raise classifier.MatchError("jev down")
    classifier.post_json = jev_down
    for call in (lambda: classifier.is_spam("jbl speaker"), lambda: classifier.match("wired earphones", L)):
        try:
            call()
            raise AssertionError("Jev down must raise JevDown")
        except classifier.JevDown:
            pass
    assert classifier.jev_up() is False

    os.environ.pop("TYPESAFE_API_KEY")
    os.environ["VERCEL"] = "1"
    assert classifier.jev_up() is False
    os.environ.pop("VERCEL")
    assert classifier.jev_up() is True
    print("ok")


if __name__ == "__main__":
    test()
