# Smart Billion Days

Search Amazon.in and Flipkart at once, and see only the products that match what you asked for.

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2FSELECT-stupidity-FROM-discord%2Fsmart-billion-days&project-name=smart-billion-days&repository-name=smart-billion-days&env=TYPESAFE_API_KEY,AMAZON_FETCH_SECRET&envDescription=TypeSafe%20API%20key%20for%20Jev%2C%20and%20any%20long%20random%20string%20to%20protect%20the%20Amazon%20function&envLink=https%3A%2F%2Fgithub.com%2FSELECT-stupidity-FROM-discord%2Fsmart-billion-days%23environment-variables)

## How it works

1. Both stores are searched in parallel: Flipkart over plain HTTP, Amazon in a headless browser.
2. A small LLM (Mistral Nemo, via the Vercel AI Gateway) turns the search into a short list of criteria.
3. Jev (TypeSafe System One) checks every listing against every criterion, and only the matches are shown.

If Jev is unreachable, the site switches to a maintenance page and comes back on its own once Jev responds again.

## Environment variables

| Name | Required | What it is |
|---|---|---|
| `TYPESAFE_API_KEY` | Yes | API key for Jev |
| `AMAZON_FETCH_SECRET` | Yes | Any long random string; stops others using the Amazon function |
| `AI_GATEWAY_API_KEY` | No | Not needed on Vercel, which signs gateway calls automatically. Needed to run locally |
| `VERCEL_AUTOMATION_BYPASS_SECRET` | No | Only for preview deployments behind Vercel Authentication |

## Run locally

Needs Python 3.9+ and Google Chrome.

```sh
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
TYPESAFE_API_KEY=... AI_GATEWAY_API_KEY=... .venv/bin/python server.py
```

Open http://127.0.0.1:8000. Without `TYPESAFE_API_KEY`, results are shown unfiltered.

## Tests

```sh
cd backend
.venv/bin/python test_scraper.py && .venv/bin/python test_price.py
.venv/bin/python test_classifier.py && .venv/bin/python test_server.py
```
