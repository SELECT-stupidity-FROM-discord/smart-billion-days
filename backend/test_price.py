from scraper import amazon_url, flipkart_url, split_price

CASES = {
    "IEM above 5000": ("IEM", 5000, None),
    "boat earphones under 1000": ("boat earphones", None, 1000),
    "noise cancelling headphones below 5k": ("noise cancelling headphones", None, 5000),
    "laptop between 40000 and 60000": ("laptop", 40000, 60000),
    "phone 15k-20k": ("phone", 15000, 20000),
    "tv ₹30,000 to ₹50,000": ("tv", 30000, 50000),
    "watch under rs 2,500": ("watch", None, 2500),
    "smartwatch 2000+": ("smartwatch", 2000, None),
    "sofa under 1.5 lakh": ("sofa", None, 150000),
    "earbuds for under 999 rs": ("earbuds", None, 999),
    "shoes price below 3000": ("shoes", None, 3000),
    "under 500": ("under 500", None, 500),
    "samsung 55 inch 4k tv": ("samsung 55 inch 4k tv", None, None),
    "mi power bank 20000mah": ("mi power bank 20000mah", None, None),
    "steel water bottle 1 litre": ("steel water bottle 1 litre", None, None),
    "pressure cooker 2-3 litre": ("pressure cooker 2-3 litre", None, None),
    "over ear headphones": ("over ear headphones", None, None),
    "usb cable under 2 m": ("usb cable under 2 m", None, None),
    "iphone 15 case": ("iphone 15 case", None, None),
    "mixer grinder 750w": ("mixer grinder 750w", None, None),
}

for q, want in CASES.items():
    got = split_price(q)
    assert got == want, f"{q!r}: got {got}, want {want}"
assert amazon_url("iem", 2, 5000, None, "rating", 4, 30) == ("https://www.amazon.in/s?k=iem&page=2&low-price=5000&high-price="
                                                         "&s=review-rank&rh=p_72%3A1318476031&pct-off=30-&dc")
assert flipkart_url("iem", 1) == "https://www.flipkart.com/search?q=iem&page=1"
assert "sort=price_asc" in flipkart_url("iem", 1, sort="price-asc") and "4%E2%98%85" in flipkart_url("iem", 1, rating=4)
print(f"ok: {len(CASES)} queries, store URLs")
