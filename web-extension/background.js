// Holds the most recent autofill payload until the job page picks it up,
// and turns the toolbar button into "send this job to the pipeline".

const APP_URL = "https://job-engine-c8f9c.web.app";

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

// Toolbar click on ANY job page (LinkedIn, Indeed, a company site...):
// open the queue with the URL prefilled — the signed-in web app calls
// add_job_url itself, so the extension never needs credentials.
chrome.action.onClicked.addListener((tab) => {
  if (!tab?.url || !/^https?:/.test(tab.url)) return;
  chrome.tabs.create({
    url: `${APP_URL}/?add=${encodeURIComponent(tab.url)}`,
  });
});
