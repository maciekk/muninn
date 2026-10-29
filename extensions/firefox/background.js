/* Explicitly connected worker; only its own dedicated tab is navigated/read. */
let enabled = false;
let token = "";
let workerTab = null;
let status = "Disconnected";
let busy = false;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function api(path, pairingToken, payload) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`http://127.0.0.1:8765${path}`, {
      method: payload === undefined ? "GET" : "POST",
      headers: {"Authorization": `Bearer ${pairingToken}`, "Content-Type": "application/json"},
      ...(payload === undefined ? {} : {body: JSON.stringify(payload)}),
      signal: controller.signal
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Bridge error ${response.status}`);
    return result;
  } finally {
    clearTimeout(timer);
  }
}

function allowedURL(url) {
  const parsed = new URL(url);
  return parsed.protocol === "https:" && ["newegg.ca", "www.newegg.ca"].includes(parsed.hostname) &&
    !parsed.username && !parsed.password && (!parsed.port || parsed.port === "443");
}

async function collect(job) {
  if (!allowedURL(job.url)) throw new Error("Refusing a non-Newegg URL");
  if (workerTab !== null) {
    try { await browser.tabs.get(workerTab); }
    catch (_) { workerTab = null; }
  }
  if (workerTab === null) {
    workerTab = (await browser.tabs.create({url: job.url, active: true})).id;
  } else {
    const tab = await browser.tabs.get(workerTab);
    if (tab.url === job.url) await browser.tabs.reload(workerTab);
    else await browser.tabs.update(workerTab, {url: job.url});
  }
  status = "Loading Newegg; complete any verification in the dedicated Muninn tab.";
  // Give navigation time to start, then wait for a stable, visible main price.
  await sleep(2000);
  const deadline = Date.now() + 150000;
  let previous = "";
  let stable = 0;
  while (Date.now() < deadline) {
    if (!enabled) throw new Error("Firefox collection was disconnected");
    const tab = await browser.tabs.get(workerTab);
    if (tab.status === "complete") {
      let page;
      try {
        [page] = await browser.tabs.executeScript(workerTab, {code: `(() => {
          const selectors = [".product-price--x .price-current_2026", ".product-price .price-current_2026",
            ".product-price--x .price-current", ".product-price .price-current"];
          let price = "";
          for (const selector of selectors) {
            for (const node of document.querySelectorAll(selector)) {
              if (node.getClientRects().length && getComputedStyle(node).visibility !== "hidden" && /[0-9]/.test(node.textContent)) {
                price = node.textContent.trim(); break;
              }
            }
            if (price) break;
          }
          return {url: location.href, price, html: price ? document.documentElement.outerHTML : ""};
        })()`});
      } catch (_) {
        // Verification may temporarily navigate to a page we cannot read.
      }
      if (page && page.price && page.url.split("#")[0] === job.url.split("#")[0]) {
        stable = page.price === previous ? stable + 1 : 1;
        previous = page.price;
        if (stable >= 3) {
          const result = {id: job.id, url: page.url, html: page.html};
          if (new TextEncoder().encode(JSON.stringify(result)).length > 10 * 1024 * 1024) {
            throw new Error("Rendered page exceeds the bridge's 10 MiB limit");
          }
          return result;
        }
      } else {
        previous = "";
        stable = 0;
      }
    }
    await sleep(1000);
  }
  throw new Error("No stable main-product price at the requested URL after 150 seconds; check verification, variant and availability");
}

async function poll() {
  if (!enabled || busy) return;
  busy = true;
  const pairingToken = token;
  try {
    const {job} = await api("/next", pairingToken);
    if (job) {
      let result;
      try { result = await collect(job); }
      catch (error) { result = {id: job.id, error: error.message}; }
      await api("/result", pairingToken, result);
      status = result.error ? `Collection failed: ${result.error}` : "Connected — last page collected";
    }
  } catch (error) {
    enabled = false;
    status = `Disconnected: ${error.message}. Restart the bridge if needed, then reconnect.`;
  } finally {
    busy = false;
  }
}
setInterval(poll, 1000);

browser.runtime.onMessage.addListener(async message => {
  if (message.action === "connect") {
    if (busy) return {error: "Worker is busy; wait for the current request to finish"};
    try {
      const pairingToken = message.token.trim();
      if (!pairingToken) throw new Error("Paste the pairing token first");
      await api("/status", pairingToken);
      token = pairingToken;
      await browser.storage.local.set({token});
      enabled = true;
      status = "Connected — ready for muninn check";
      poll();
    } catch (error) {
      enabled = false;
      status = `Connection failed: ${error.message}`;
      return {error: error.message};
    }
  } else if (message.action === "disconnect") {
    enabled = false;
    status = "Disconnected";
  }
  return {enabled, status};
});
