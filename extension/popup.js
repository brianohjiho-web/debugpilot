const $ = (id) => document.getElementById(id);
let pageUrl = "";

function say(text, kind) { $("msg").textContent = text; $("msg").className = "msg " + (kind || ""); }

async function init() {
  // The page to scan: the tab you're on. (?url= is only used when testing the popup in a normal tab.)
  const test = new URLSearchParams(location.search).get("url");
  if (test) pageUrl = test;
  else {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    pageUrl = (tab && tab.url) || "";
  }
  try { const u = new URL(pageUrl); $("host").textContent = u.hostname || pageUrl; } catch (e) { $("host").textContent = "This page"; }
  $("url").textContent = pageUrl;
  if (!scannable(pageUrl)) {
    $("scan").disabled = true;
    say("This kind of page can't be scanned. Open a normal website first.", "err");
  }
  $("server").value = await getServer();

  // A right-click scan that failed leaves a note here.
  const { lastError } = await chrome.storage.local.get("lastError");
  if (lastError) { say(lastError, "err"); chrome.storage.local.remove("lastError"); chrome.action.setBadgeText({ text: "" }); }
}

$("scan").onclick = async () => {
  $("scan").disabled = true;
  say("Starting the scan…");
  try {
    const report = await startScan(pageUrl);
    say("Scan started. Opening the report…", "ok");
    await chrome.tabs.create({ url: report });
    window.close();
  } catch (e) {
    say(e.message, "err");
    $("scan").disabled = false;
  }
};

$("save").onclick = async () => {
  const value = $("server").value.trim().replace(/\/+$/, "") || "http://localhost:8000";
  if (!/^https?:\/\//.test(value)) { say("The address should start with http:// or https://", "err"); return; }
  // A DebugPilot that runs online needs permission to talk to it.
  if (!/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(value)) {
    const ok = await chrome.permissions.request({ origins: [value + "/*"] }).catch(() => false);
    if (!ok) { say("Chrome didn't allow that address.", "err"); return; }
  }
  await chrome.storage.sync.set({ server: value });
  say("Saved.", "ok");
};

init();
