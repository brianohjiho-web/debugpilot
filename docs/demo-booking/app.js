function show(text, ok) { const m = document.getElementById("msg"); m.textContent = text; m.className = "msg " + (ok ? "ok" : ""); }
document.getElementById("price").onclick = () => show("Price per night: $" + priceFor(document.getElementById("room").value), true);
document.getElementById("badge").onclick = () => show("Name badge: " + guestInitials(document.getElementById("name").value), true);
document.getElementById("times").onclick = () => show("Free times: " + freeTimes(document.getElementById("day").value).join(", "), true);
window.addEventListener("error", () => show("Something went wrong. Please try again."));
