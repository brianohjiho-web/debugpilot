importScripts("scan.js");

// Right-click anywhere on a page → "Scan this page with DebugPilot".
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "dp-scan", title: "Scan this page with DebugPilot", contexts: ["page"] });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "dp-scan" || !tab) return;
  if (!scannable(tab.url)) return;
  try {
    const report = await startScan(tab.url);
    chrome.tabs.create({ url: report, index: tab.index + 1 });
  } catch (e) {
    // Show the problem in the popup next time it opens.
    chrome.storage.local.set({ lastError: e.message });
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#ff8a7a" });
  }
});
