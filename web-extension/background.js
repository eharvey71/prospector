// Holds the most recent autofill payload until the job page picks it up.
chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg.kind === "store") {
    chrome.storage.local.set({ pending: msg.payload }, () => respond({ ok: true }));
    return true; // async respond
  }
  if (msg.kind === "clear") {
    chrome.storage.local.remove("pending", () => respond({ ok: true }));
    return true;
  }
});
