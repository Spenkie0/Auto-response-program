const NATIVE_APP = "scrapper_ollama";
let busy = false;

/**
 * Capture the supplied Firefox tab DOM and hand it to the native Python host.
 * @param {Object} tab - Firefox tab object supplied by browserAction.onClicked.
 * @returns {Promise<void>} Resolves after the native host accepts the capture.
 */
async function captureTabAndStart(tab) {
  if (busy) {
    return;
  }

  if (!tab || typeof tab.id !== "number") {
    throw new Error("Firefox did not provide an active tab.");
  }

  busy = true;
  await browser.browserAction.setBadgeText({text: "…"});
  await browser.browserAction.setTitle({title: "Capturing active Firefox tab…"});

  try {
    const results = await browser.tabs.executeScript(tab.id, {
      code: `({
        url: location.href,
        title: document.title,
        html: document.documentElement
          ? document.documentElement.outerHTML
          : ""
      })`
    });

    const capture = results && results[0];
    if (!capture || typeof capture.html !== "string" || !capture.html) {
      throw new Error("The active tab did not return an HTML DOM.");
    }

    // Capture the visible tab at the same user-triggered moment as the DOM.
    // Firefox's activeTab permission is sufficient for captureVisibleTab.
    const screenshotDataUrl = await browser.tabs.captureVisibleTab(tab.windowId, {
      format: "png"
    });

    const port = browser.runtime.connectNative(NATIVE_APP);
    const responsePromise = new Promise((resolve, reject) => {
      port.onMessage.addListener((response) => resolve(response));
      port.onDisconnect.addListener(() => {
        const message = browser.runtime.lastError
          ? browser.runtime.lastError.message
          : "Native host disconnected before returning a result.";
        reject(new Error(message));
      });
    });

    port.postMessage({
      action: "capture",
      url: capture.url || tab.url || "",
      title: capture.title || tab.title || "",
      html: capture.html,
      screenshot_data_url: screenshotDataUrl
    });

    const response = await responsePromise;
    port.disconnect();

    if (!response || response.ok !== true) {
      throw new Error((response && response.error) || "The native host reported an error.");
    }

    await browser.browserAction.setBadgeText({text: "✓"});
    await browser.browserAction.setTitle({
      title: `Capture started: ${response.raw_file || capture.title || "page"}`
    });
  } catch (error) {
    await browser.browserAction.setBadgeText({text: "!"});
    await browser.browserAction.setTitle({title: `Scraper error: ${String(error)}`});
    console.error("Scrapper Ollama trigger failed:", error);
  } finally {
    busy = false;
    setTimeout(() => browser.browserAction.setBadgeText({text: ""}), 5000);
  }
}

browser.browserAction.onClicked.addListener((tab) => {
  captureTabAndStart(tab).catch((error) => {
    console.error("Unexpected scraper trigger failure:", error);
    busy = false;
  });
});
