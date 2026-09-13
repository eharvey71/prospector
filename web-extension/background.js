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
  if (msg.kind === "follow_me") {
    // The panel appeared on its own (domain matched the job); remember the
    // tab so it keeps appearing after the apply-now hop to another domain.
    if (_sender.tab?.id != null) followTab(_sender.tab.id);
    respond?.({ ok: true });
    return false;
  }
  if (msg.kind === "store") {
    chrome.storage.local.set({ pending: msg.payload }, () => {
      setBadge();
      respond({ ok: true });
    });
    return true; // async respond
  }
  if (msg.kind === "clear") {
    chrome.storage.local.remove("pending", async () => {
      await chrome.storage.session.remove("followTabs");  // stop following
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

// Tabs where the panel has been summoned. An application is a JOURNEY —
// job board -> "Apply now" -> the ATS on another domain, sometimes a
// login in between — and the panel's domain check only covers the job's
// own site. Once summoned in a tab, follow that tab wherever it goes
// until the job is cleared.
async function followedTabs() {
  const { followTabs } = await chrome.storage.session.get("followTabs");
  return new Set(followTabs || []);
}

async function followTab(tabId) {
  const tabs = await followedTabs();
  tabs.add(tabId);
  await chrome.storage.session.set({ followTabs: [...tabs] });
}

async function summonPanel(tab, remember = true) {
  // Returns false when no content script is listening (chrome:// pages,
  // the Web Store, PDF viewer) so callers can fall back.
  try {
    await chrome.tabs.sendMessage(tab.id, { kind: "show_panel" }, { frameId: 0 });
    if (remember) await followTab(tab.id);
    return true;
  } catch {
    return false;
  }
}

// Re-show the panel after each real navigation in a followed tab.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (changeInfo.status !== "complete") return;
  const tabs = await followedTabs();
  if (!tabs.has(tabId)) return;
  const { pending } = await chrome.storage.local.get("pending");
  if (!pending?.url) return;
  summonPanel({ id: tabId }, false);
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const tabs = await followedTabs();
  if (tabs.delete(tabId)) {
    await chrome.storage.session.set({ followTabs: [...tabs] });
  }
});

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
