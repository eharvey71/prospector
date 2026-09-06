// Holds the most recent autofill payload, and drives the toolbar button.
//
// The button does the obvious thing for the situation:
//   - a job is loaded  -> summon the autofill panel on THIS page, wherever
//     you've clicked through to (LinkedIn -> careers page -> the ATS three
//     domains later). The panel's own domain check is for automatic
//     appearances; an explicit click is intent, so it's skipped.
//   - nothing loaded   -> send this page to the queue.
// Both actions are also always available explicitly by right-click.

const APP_URL = "https://job-engine-c8f9c.web.app";

async function setBadge() {
  const { pending } = await chrome.storage.local.get("pending");
  await chrome.action.setBadgeText({ text: pending?.url ? "•" : "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#6f9ff3" });
  await chrome.action.setTitle({
    title: pending?.url
      ? `Fill this page with: ${pending.title || "your prepared answers"}`
      : "Send this job to Prospector",
  });
}

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg.kind === "store") {
    chrome.storage.local.set({ pending: msg.payload }, () => {
      setBadge();
      respond({ ok: true });
    });
    return true; // async respond
  }
  if (msg.kind === "clear") {
    chrome.storage.local.remove("pending", () => {
      setBadge();
      respond({ ok: true });
    });
    return true;
  }
});

function addToQueue(url) {
  if (!url || !/^https?:/.test(url)) return;
  chrome.tabs.create({ url: `${APP_URL}/?add=${encodeURIComponent(url)}` });
}

async function summonPanel(tab) {
  // Returns false when no content script is listening (chrome:// pages,
  // the Web Store, PDF viewer) so callers can fall back.
  try {
    await chrome.tabs.sendMessage(tab.id, { kind: "show_panel" }, { frameId: 0 });
    return true;
  } catch {
    return false;
  }
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab?.url || !/^https?:/.test(tab.url)) return;
  const { pending } = await chrome.storage.local.get("pending");
  if (pending?.url && await summonPanel(tab)) return;
  addToQueue(tab.url);
});

// Explicit right-click actions — either one is always reachable, even when
// the button is busy meaning the other thing.
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "je-fill", contexts: ["page", "editable", "selection"],
    title: "Fill this form with Prospector",
  });
  chrome.contextMenus.create({
    id: "je-add", contexts: ["page", "link"],
    title: "Add this job to Prospector",
  });
  setBadge();
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "je-fill") {
    if (!(await summonPanel(tab))) {
      chrome.tabs.create({ url: `${APP_URL}/extension` });  // likely not installed here
    }
  } else if (info.menuItemId === "je-add") {
    addToQueue(info.linkUrl || tab?.url);
  }
});

chrome.runtime.onStartup.addListener(setBadge);
