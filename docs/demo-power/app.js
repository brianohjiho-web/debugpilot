function show(text, ok) { const m = document.getElementById("msg"); m.textContent = text; m.className = "msg " + (ok ? "ok" : ""); }
document.querySelectorAll("[data-rack]").forEach((b) => (b.onclick = () => show("Rack " + b.dataset.rack + ": " + rackLoad(b.dataset.rack), true)));
document.querySelectorAll("[data-level]").forEach((b) => (b.onclick = () => show("Top alarm: " + topAlarm(b.dataset.level), true)));
document.getElementById("convert").onclick = () => show("Total IT load: " + inUnits(13.5, document.getElementById("unit").value), true);
window.addEventListener("error", () => show("This panel failed to load. Check the monitoring system."));
