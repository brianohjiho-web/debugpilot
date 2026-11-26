// Shared by the popup and the right-click menu: talk to the DebugPilot server.
const DEFAULT_SERVER = "https://debugpilot-x4kn.onrender.com";

async function getServer() {
  const { server } = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  return (server || DEFAULT_SERVER).replace(/\/+$/, "");
}

function scannable(url) {
  return /^https?:\/\//i.test(url || "");
}

// Starts a scan and returns the report page's address.
async function startScan(url) {
  const server = await getServer();
  let res;
  try {
    res = await fetch(server + "/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  } catch (e) {
    throw new Error("Can't reach DebugPilot at " + server + ". Check your internet and try again (the free server may take a minute to wake up).");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "The scan couldn't start (error " + res.status + ").");
  return server + "/report/" + data.id;
}
