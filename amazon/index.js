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

function send(res, code, body, type = "text/plain; charset=utf-8") {
  res.statusCode = code;
  res.setHeader("Content-Type", type);
  res.end(body);
}

module.exports = async (req, res) => {
  const query = new URL(req.url, "http://amazon").searchParams;
  const q = (query.get("k") || "").trim().slice(0, 80);
  const page = Number(query.get("page") || 1);
  if (!q || !Number.isInteger(page) || page < 1 || page > 20) return send(res, 400, "bad k or page");
  const url = new URL("https://www.amazon.in/s");
  url.searchParams.set("k", q);
  url.searchParams.set("page", page);
  for (const [name, shape] of Object.entries(PARAMS)) {
    if (!query.has(name)) continue;
    const v = query.get(name);
    if (!shape.test(v)) return send(res, 400, `bad ${name}`);
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
    send(res, 200, await tab.content(), "text/html; charset=utf-8");
  } catch (e) {
    const old = browser;
    browser = null;
    if (old) old.close().catch(() => {});
    send(res, 502, String(e && e.message));
  } finally {
    if (tab) tab.close().catch(() => {});
  }
};
