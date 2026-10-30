/* Manual import remains available alongside the background retailer worker. */
const tokenInput = document.querySelector("#token");
const button = document.querySelector("#import");
const status = document.querySelector("#status");

browser.storage.local.get("token").then(saved => {
  tokenInput.value = saved.token || "";
}).catch(error => { status.textContent = error.message; });

button.addEventListener("click", async () => {
  button.disabled = true;
  status.textContent = "Reading current tab…";
  try {
    const token = tokenInput.value.trim();
    if (!token) throw new Error("Paste the pairing token from Muninn first.");
    const [tab] = await browser.tabs.query({active: true, currentWindow: true});
    const [page] = await browser.tabs.executeScript(tab.id, {
      code: "({url: location.href, html: document.documentElement.outerHTML})"
    });
    if (!page || !/^https?:\/\//.test(page.url)) {
      throw new Error("Open a configured product page first.");
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    let response;
    try {
      response = await fetch("http://127.0.0.1:8765/import", {
        method: "POST",
        headers: {"Content-Type": "application/json", "Authorization": `Bearer ${token}`},
        body: JSON.stringify(page),
        signal: controller.signal
      });
    } finally {
      clearTimeout(timeout);
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Import failed: ${response.status}`);
    await browser.storage.local.set({token});
    status.textContent = `Imported $${result.price}. Run muninn check to see the updated report.`;
  } catch (error) {
    status.textContent = error instanceof TypeError
      ? `Cannot reach the local bridge: ${error.message}. Check that muninn browser-bridge is running.`
      : error.message;
  } finally {
    button.disabled = false;
  }
});

const workerStatus = document.querySelector("#worker-status");
async function workerAction(action) {
  try {
    const result = await browser.runtime.sendMessage({action, token: tokenInput.value});
    workerStatus.textContent = result.error || result.status;
  } catch (error) {
    workerStatus.textContent = error.message;
  }
}
document.querySelector("#connect").addEventListener("click", () => workerAction("connect"));
document.querySelector("#disconnect").addEventListener("click", () => workerAction("disconnect"));
workerAction("status");
setInterval(() => workerAction("status"), 1000);
