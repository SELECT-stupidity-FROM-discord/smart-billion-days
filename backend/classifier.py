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
          "excluding price. Fill the three lines. Copy only what the search says; never add anything it doesn't mention. "
          "Write none when the search doesn't say.")
EXAMPLES = [
    ("wired earphones", "Product: earphones\nBrand: none\nAlso: connects with a cable, not wireless"),
    ("jbl speaker under 2000", "Product: speaker\nBrand: JBL\nAlso: none"),
    ("mixer grinder", "Product: mixer grinder\nBrand: none\nAlso: none"),
    ("iphone 15 case", "Product: phone case\nBrand: none\nAlso: fits the iPhone 15"),
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


def parse_criteria(text: str) -> list:
    out = []
    for line in text.replace("*", "").splitlines():
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip().rstrip(".")
        if not val or val.lower() == "none":
            continue
        if key == "product":
            items = [f"Is {val}"]
        elif key == "brand":
            items = [f"Made by {val}"]
        else:
            items = [v.strip() for v in val.split(",") if v.strip().lower() not in ("", "none")]
        out += [c for c in items if c not in out and not PRICE.search(c)]
    return out[:MAX_CRITERIA]


@lru_cache(maxsize=512)
def criteria(query: str) -> tuple:
    key = os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("VERCEL_OIDC_TOKEN")
    data = post_json(LLM_URL, {
        "model": LLM_MODEL,
        "temperature": 0,
        "max_tokens": 80,
        "messages": MESSAGES + [{"role": "user", "content": query}],
    }, {"Authorization": f"Bearer {key}"} if key else {})
    try:
        found = parse_criteria(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise MatchError(f"LLM reply had no text: {str(data)[:200]}") from e
    if not found:
        raise MatchError("LLM gave no criteria")
    return tuple(found)


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


def jev_keep(crit: tuple, listings: list) -> list:
    questions = {}
    for i, x in enumerate(listings):
        listing = {"title": x["title"], "brand": x.get("brand"), "store category": x.get("category"), "variant": x.get("variant")}
        for j, c in enumerate(crit):
            questions[f"l{i}c{j}"] = {
                "type": "noul",
                "instructions": {"listing": listing, "question": f"Does `listing` meet this requirement: {c}"},
                "criteria": {
                    "true": "The listing meets it, or very likely does given its title, brand, category and variant",
                    "false": "The listing clearly doesn't meet it, or is a different kind of product or an accessory",
                },
            }
    a = jev({"shopper wants": list(crit)}, questions)
    return [[c for j, c in enumerate(crit) if a[f"l{i}c{j}"]["noul"] < KEEP_AT] for i in range(len(listings))]


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
        crit = criteria(query.lower())
        failed = jev_keep(crit, listings)
    except JevDown:
        raise
    except MatchError as e:
        print(f"matching skipped for {query!r}: {e}")
        return {"listings": listings, "filtered": [], "criteria": None, "left_out": 0, "error": str(e)}
    kept = [x for x, f in zip(listings, failed) if not f]
    filtered = [{**x, "failed": f} for x, f in zip(listings, failed) if f]
    return {"listings": kept, "filtered": filtered, "criteria": list(crit), "left_out": len(filtered), "error": None}
