const chromium = require("@sparticuz/chromium");
const puppeteer = require("puppeteer-core");

const LOCAL_CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const SKIP = new Set(["image", "media", "font"]);
const PARAMS = {
  "low-price": /^\d{0,8}$/, "high-price": /^\d{0,8}$/,
  s: /^[a-z-]{1,40}$/, rh: /^p_72:\d{1,12}$/, "pct-off": /^\d{1,2}-$/,
};

let browser;

async function getBrowser() {
  if (browser && browser.connected) return browser;
  browser = await puppeteer.launch(process.env.VERCEL
    ? { args: chromium.args, executablePath: await chromium.executablePath(), headless: true }
    : { executablePath: LOCAL_CHROME, headless: true });
  return browser;
}

module.exports = async (req, res) => {
  const secret = process.env.AMAZON_FETCH_SECRET;
  if (process.env.VERCEL && (!secret || req.headers["x-internal-key"] !== secret)) {
    return res.status(403).send("forbidden");
  }
  const q = String(req.query.k || "").trim().slice(0, 80);
  const page = Number(req.query.page || 1);
  if (!q || !Number.isInteger(page) || page < 1 || page > 20) return res.status(400).send("bad k or page");
  const url = new URL("https://www.amazon.in/s");
  url.searchParams.set("k", q);
  url.searchParams.set("page", page);
  for (const [name, shape] of Object.entries(PARAMS)) {
    if (!(name in req.query)) continue;
    const v = String(req.query[name]);
    if (!shape.test(v)) return res.status(400).send(`bad ${name}`);
    url.searchParams.set(name, v);
  }

  let tab;
  try {
    tab = await (await getBrowser()).newPage();
    await tab.setRequestInterception(true);
    tab.on("request", (r) => (SKIP.has(r.resourceType()) ? r.abort() : r.continue()));
    await tab.goto(`${url}&dc`, {
      waitUntil: "domcontentloaded",
      timeout: 20000,
    });
    await tab.waitForFunction(
      () => document.querySelector('div[data-component-type="s-search-result"]') ||
        (document.body && document.body.innerText.includes("No results for")),
      { timeout: 12000 },
    ).catch(() => {});
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.status(200).send(await tab.content());
  } catch (e) {
    const old = browser;
    browser = null;
    if (old) old.close().catch(() => {});
    res.status(502).send(String(e && e.message));
  } finally {
    if (tab) tab.close().catch(() => {});
  }
};
