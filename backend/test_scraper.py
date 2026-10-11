from pathlib import Path

from scraper import StoreUnavailable, discount, parse_amazon, parse_flipkart, rupees

FIX = Path(__file__).parent / "fixtures"


def check(listings, store):
    assert len(listings) >= 10, f"{store}: only {len(listings)} listings"
    assert len({x.id for x in listings}) == len(listings), f"{store}: duplicate ids"
    for x in listings:
        assert x.store == store and x.title and x.price > 0, x
        assert x.url.startswith("https://"), x.url
        assert x.mrp is None or x.mrp >= x.price, x
        assert x.image is None or x.image.startswith("https://"), x.image


def test():
    assert rupees("M.R.P: ₹1,299") == 1299 and rupees("₹339.02") == 339 and rupees(None) is None
    assert discount(599, 1299) == 54 and discount(500, None) is None and discount(500, 400) is None

    amazon = parse_amazon((FIX / "amazon_wired_earphones.html").read_text(encoding="utf-8"))
    check(amazon, "amazon")
    jbl = next(x for x in amazon if "JBL C100SI" in x.title)
    assert (jbl.price, jbl.mrp, jbl.discount_pct) == (599, 1299, 54), jbl

    flipkart = parse_flipkart((FIX / "flipkart_wired_earphones.html").read_text(encoding="utf-8"))
    check(flipkart, "flipkart")
    assert any(x.category == "WiredEarphone" for x in flipkart)

    blocked = (
        (parse_amazon, "<html>validateCaptcha</html>"),
        (parse_amazon, '<meta http-equiv="refresh" content="5; URL=\'/s?k=x&bm-verify=AAQ\'">'),
        (parse_flipkart, "<html></html>"),
    )
    for parse, page in blocked:
        try:
            parse(page)
            raise AssertionError(f"{parse.__name__} accepted a blocked page")
        except StoreUnavailable:
            pass

    print(f"ok: {len(amazon)} amazon, {len(flipkart)} flipkart")


if __name__ == "__main__":
    test()
