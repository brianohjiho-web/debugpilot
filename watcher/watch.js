/*
 * DebugPilot watcher. Add one line to a website:
 *   <script src="https://YOUR-DEBUGPILOT/watch.js" data-key="PROJECT_KEY" defer></script>
 *
 * It catches JavaScript errors that real visitors hit and sends them to DebugPilot.
 * It sends no form data, cookies or page content: only the error, where it happened,
 * the page address and the browser name. It is small on purpose and never throws.
 */
(function () {
  "use strict";
  try {
    var me = document.currentScript || document.querySelector("script[data-key][src*='watch.js']");
    if (!me) return;
    var key = me.getAttribute("data-key");
    if (!key) return;
    var endpoint = me.getAttribute("data-endpoint") || new URL("/api/ingest", me.src).href;

    var sent = 0, MAX_PER_PAGE = 20;      // never flood the server from one page
    var seen = {};                       // the same error once per page view

    function send(payload) {
      if (sent >= MAX_PER_PAGE) return;
      var sig = payload.type + "|" + payload.message + "|" + (payload.stack || "").split("\n")[1];
      if (seen[sig]) return;
      seen[sig] = 1; sent++;
      payload.key = key;
      payload.page = location.href.split("#")[0].split("?")[0];
      payload.browser = navigator.userAgent.slice(0, 200);
      payload.at = new Date().toISOString();
      var body = JSON.stringify(payload);
      try {
        if (navigator.sendBeacon && navigator.sendBeacon(endpoint, new Blob([body], { type: "text/plain" }))) return;
      } catch (e) {}
      try { fetch(endpoint, { method: "POST", body: body, keepalive: true, mode: "cors", headers: { "Content-Type": "text/plain" } }); } catch (e) {}
    }

    function fromError(err, fallbackMsg) {
      var e = err || {};
      return {
        type: (e && e.name) || "Error",
        message: String((e && e.message) || fallbackMsg || "Unknown error").slice(0, 500),
        stack: String((e && e.stack) || "").slice(0, 4000)
      };
    }

    window.addEventListener("error", function (ev) {
      // Errors from images or scripts that failed to load have no error object; skip those.
      if (!ev || (!ev.error && !ev.message)) return;
      var p = fromError(ev.error, ev.message);
      if (!p.stack && ev.filename) p.stack = p.type + ": " + p.message + "\n    at " + ev.filename + ":" + ev.lineno + ":" + ev.colno;
      send(p);
    });
    window.addEventListener("unhandledrejection", function (ev) {
      var r = ev && ev.reason;
      send(r instanceof Error ? fromError(r) : { type: "UnhandledRejection", message: String(r).slice(0, 500), stack: "" });
    });

    // Lets a page report a caught error on purpose: window.DebugPilot.report(err)
    window.DebugPilot = { report: function (err) { send(fromError(err)); } };
  } catch (e) { /* the watcher must never break the site */ }
})();
