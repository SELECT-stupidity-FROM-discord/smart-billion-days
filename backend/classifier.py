import json
import os
import re
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LLM_URL = os.environ.get("LLM_URL", "https://ai-gateway.vercel.sh/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "mistral/mistral-nemo")
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
SPAM_AT = 0.7
KEEP_AT = 0.5
MAX_CRITERIA = 5
PRICE = re.compile(r"price|budget|cost|₹|\brs\.?\b|\binr\b|\b(under|below|less than|within|upto|up to)\s*\d", re.I)
TIMEOUT = 12

SYSTEM = ("A shopper searched Amazon.in and Flipkart in India. Define the minimum criteria for the product they want, "
          "excluding price. Fill the five lines. Product names what they want. For Brand and Also, copy only what the "
          "search says, and write none when it doesn't say. Is defines the product the way a dictionary would, so "
          "anyone could recognise one. Is not names clearly different products that look similar, and accessories; "
          "never a common type of the product itself.")
EXAMPLES = [
    ("wired earphones",
     "Product: earphones\n"
     "Brand: none\n"
     "Also: connects with a cable, not wireless\n"
     "Is: Small audio devices worn in or just outside the ear canal, sold as a pair\n"
     "Is not: Over-ear or on-ear headphones, speakers, or accessories such as ear tips, cases or cables"),
    ("jbl speaker under 2000",
     "Product: speaker\n"
     "Brand: JBL\n"
     "Also: none\n"
     "Is: A device that plays sound out loud into a room, portable or plugged in\n"
     "Is not: Headphones or earphones worn on the body, or accessories such as stands, covers or cables"),
    ("mixer grinder",
     "Product: mixer grinder\n"
     "Brand: none\n"
     "Also: none\n"
     "Is: A kitchen appliance with a motor base and detachable jars that grinds spices and blends wet and dry food\n"
     "Is not: A juicer, a hand blender, or spare parts such as jars, blades or couplers"),
    ("iphone 15 case",
     "Product: phone case\n"
     "Brand: none\n"
     "Also: fits the iPhone 15\n"
     "Is: A cover that wraps the back and sides of a phone to protect it\n"
     "Is not: A screen protector, tempered glass, a charger, or a skin sticker"),
]

MESSAGES = [{"role": "system", "content": SYSTEM}] + [
    m for q, a in EXAMPLES for m in ({"role": "user", "content": q}, {"role": "assistant", "content": a})]


class MatchError(Exception):
    pass


class JevDown(MatchError):
    pass


def post_json(url: str, body: dict, headers: dict) -> dict:
    req = Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except HTTPError as e:
        raise MatchError(f"{url} HTTP {e.code}: {e.read()[:200]!r}") from e
    except (URLError, TimeoutError, ValueError) as e:
        raise MatchError(f"{url}: {e}") from e


GENERIC = {
    "true": "The listing meets it, or very likely does given its title, brand, category and variant",
    "false": "The listing clearly doesn't meet it, or is a different kind of product or an accessory",
}


def parse_criteria(text: str) -> list:
    """LLM lines -> [(criterion, jev criteria)]. The product carries its own definition (Is) and disqualifiers
    (Is not), the way a Jev question should; brand and other requirements use GENERIC."""
    fields, also = {}, []
    for line in text.replace("*", "").splitlines():
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip().rstrip(".")
        if not val or val.lower() == "none":
            continue
        if key in ("product", "is", "is not", "brand"):
            fields[key] = val
        else:
            also += [v.strip() for v in val.split(",") if v.strip().lower() not in ("", "none")]
    out = []
    if "product" in fields:
        defined = "is" in fields and "is not" in fields
        out.append((f"Is {fields['product']}",
                    {"true": fields["is"], "false": fields["is not"]} if defined else GENERIC))
    if "brand" in fields:
        out.append((f"Made by {fields['brand']}", GENERIC))
    out += [(c, GENERIC) for c in also]
    seen, unique = set(), []
    for c, crit in out:
        if c not in seen and not PRICE.search(c):
            seen.add(c)
            unique.append((c, crit))
    return unique[:MAX_CRITERIA]


@lru_cache(maxsize=512)
def criteria(query: str) -> tuple:
    key = os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("VERCEL_OIDC_TOKEN")
    data = post_json(LLM_URL, {
        "model": LLM_MODEL,
        "temperature": 0,
        "max_tokens": 220,
        "messages": MESSAGES + [{"role": "user", "content": query}],
    }, {"Authorization": f"Bearer {key}"} if key else {})
    try:
        found = parse_criteria(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise MatchError(f"LLM reply had no text: {str(data)[:200]}") from e
    if not found:
        raise MatchError("LLM gave no criteria")
    return tuple((c, tuple(sorted(crit.items()))) for c, crit in found)


def jev_enabled() -> bool:
    if os.environ.get("TYPESAFE_API_KEY"):
        return True
    if os.environ.get("VERCEL"):
        raise JevDown("TYPESAFE_API_KEY is not set")
    return False


def jev(state, questions: dict) -> dict:
    try:
        data = post_json(JEV_URL, {"model": JEV_MODEL, "state": state, "questions": questions},
                         {"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"})
        answers = data["answers"]
        for k in questions:
            float(answers[k]["noul"])
        return answers
    except MatchError as e:
        raise JevDown(str(e)) from e
    except (KeyError, TypeError, ValueError) as e:
        raise JevDown(f"Jev reply missing answers: {e!r}") from e


def jev_up() -> bool:
    try:
        if jev_enabled():
            jev("Hello there!", {"ok": {"type": "noul", "instructions": "Is this a greeting?"}})
        return True
    except JevDown as e:
        print(f"jev probe failed: {e}")
        return False


def split_items(text: str) -> list:
    """ "Over-ear or on-ear headphones, earbuds, or accessories such as ear tips, cables or adapters"
    -> ["Over-ear or on-ear headphones", "earbuds", "accessories such as ear tips, cables or adapters"]"""
    head, tail = text, ""
    if "such as" in text:
        cut = text.rfind(",", 0, text.index("such as"))
        head, tail = (text[:cut], text[cut + 1:]) if cut >= 0 else ("", text)
    items = [re.sub(r"^(?:or|and)\s+", "", p.strip()) for p in head.split(",")] + [re.sub(r"^(?:or|and)\s+", "", tail.strip())]
    return [i for i in items if i]


@lru_cache(maxsize=512)
def vet_exclusions(crit: tuple) -> tuple:
    """The LLM's "Is not" sometimes names a type of the product itself (a membrane keyboard for "gaming keyboard").
    Jev checks each item against the product's definition; items that are a kind of the product are dropped."""
    (name, items_), rest = crit[0], crit[1:]
    c = dict(items_)
    if c == GENERIC:
        return crit
    items = split_items(c["false"])
    product = name[3:] if name.startswith("Is ") else name
    a = jev({"product": product, "definition": c["true"]}, {f"x{i}": {
        "type": "noul",
        "instructions": {"product": product, "definition": c["true"], "item": item,
                         "question": "Is `item` a kind of `product` that fits `definition`, so a shopper looking for `product` would accept it?"},
        "criteria": {
            "true": "It is a type, style or variant of the product and fits the definition",
            "false": "It is a different product that only looks similar, or an accessory, part or add-on",
        },
    } for i, item in enumerate(items)})
    keep = [item for i, item in enumerate(items) if a[f"x{i}"]["noul"] < KEEP_AT]
    false = ", ".join(keep) if keep else GENERIC["false"]
    return ((name, tuple(sorted({"true": c["true"], "false": false}.items()))),) + rest


def jev_keep(crit: tuple, listings: list) -> list:
    questions = {}
    for i, x in enumerate(listings):
        listing = {"title": x["title"], "brand": x.get("brand"), "store category": x.get("category"), "variant": x.get("variant")}
        for j, (c, criteria_) in enumerate(crit):
            defined = dict(criteria_) != GENERIC
            question = (f"Is `listing` one of these: {c[3:]}?" if defined
                        else f"Does `listing` meet this requirement: {c}")
            questions[f"l{i}c{j}"] = {"type": "noul", "instructions": {"listing": listing, "question": question},
                                      "criteria": dict(criteria_)}
    a = jev({"shopper wants": [c for c, _ in crit]}, questions)
    return [[c for j, (c, _) in enumerate(crit) if a[f"l{i}c{j}"]["noul"] < KEEP_AT] for i in range(len(listings))]


@lru_cache(maxsize=2048)
def is_spam(query: str) -> bool:
    if not jev_enabled():
        return False
    a = jev({"search": query}, {"spam": {
        "type": "noul",
        "instructions": "Is `search` spam or nonsense rather than a search for a product to buy?",
        "criteria": {
            "true": "Gibberish, random characters, abuse, instructions or questions aimed at an AI, "
                    "or text that isn't looking for a product",
            "false": "Looking for a product, even if short, misspelled, Hinglish, or just a brand or model name",
        },
    }})
    return a["spam"]["noul"] >= SPAM_AT


def match(query: str, listings: list) -> dict:
    if not listings or not jev_enabled():
        return {"listings": listings, "filtered": [], "criteria": None, "left_out": 0, "error": None}
    try:
        crit = vet_exclusions(criteria(query.lower()))
        failed = jev_keep(crit, listings)
    except JevDown:
        raise
    except MatchError as e:
        print(f"matching skipped for {query!r}: {e}")
        return {"listings": listings, "filtered": [], "criteria": None, "left_out": 0, "error": str(e)}
    kept = [x for x, f in zip(listings, failed) if not f]
    filtered = [{**x, "failed": f} for x, f in zip(listings, failed) if f]
    return {"listings": kept, "filtered": filtered, "criteria": [c for c, _ in crit], "left_out": len(filtered), "error": None}
