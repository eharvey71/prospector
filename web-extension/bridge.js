// Runs on the Prospector web app only. The queue page posts an autofill
// payload via window.postMessage; this forwards it into extension storage
// and acks so the page knows the extension is installed.
const VERSION = chrome.runtime.getManifest().version;

window.addEventListener("message", (ev) => {
  if (ev.source !== window) return;
  if (ev.data?.type === "JOB_ENGINE_PING") {
    // The install page asks "are you there?" — content scripts and page
    // scripts race on load, so answer pings as well as announcing below.
    window.postMessage({ type: "JOB_ENGINE_EXTENSION_PRESENT", version: VERSION }, "*");
    return;
  }
  if (ev.data?.type !== "JOB_ENGINE_AUTOFILL") return;
  // After the extension is reloaded/updated, content scripts in tabs that
  // were already open are orphaned — sendMessage throws. Tell the page
  // loudly instead of dropping the payload on the floor.
  try {
    chrome.runtime.sendMessage({ kind: "store", payload: ev.data.payload }, () => {
      if (chrome.runtime.lastError) {
        window.postMessage({ type: "JOB_ENGINE_EXTENSION_STALE" }, "*");
        return;
      }
      window.postMessage({ type: "JOB_ENGINE_AUTOFILL_ACK" }, "*");
    });
  } catch {
    window.postMessage({ type: "JOB_ENGINE_EXTENSION_STALE" }, "*");
  }
});

window.postMessage({ type: "JOB_ENGINE_EXTENSION_PRESENT", version: VERSION }, "*");
