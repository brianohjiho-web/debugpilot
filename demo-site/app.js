// Page code for the demo shop: buttons and screen updates. The cart math lives in cart.js.
let cart = {};

function money(n) { return "$" + n.toFixed(2); }

function renderCart() {
  const list = document.getElementById("cart");
  const items = Object.entries(cart);
  list.innerHTML = items.length
    ? items.map(([sku, qty]) => `<li><span>${PRODUCTS[sku].name} × ${qty}</span><span>${money(PRODUCTS[sku].price * qty)}</span></li>`).join("")
    : '<li class="empty">Your cart is empty</li>';
}

function say(text, kind) {
  const el = document.getElementById("msg");
  el.textContent = text;
  el.className = "msg " + (kind || "");
}

function checkout() {
  const code = document.getElementById("coupon").value;
  let total = subtotal(cart);
  if (code.trim()) total = applyCoupon(total, code);
  const cheapest = cheapestItem(cart);
  document.getElementById("receipt").innerHTML =
    `<div><span>Total</span><b>${money(total)}</b></div><div><span>Cheapest item</span><b>${cheapest}</b></div>`;
  say("Order placed. (Demo only, no real money.)", "ok");
}

function estimateShipping() {
  const zip = document.getElementById("zip").value;
  const days = shippingDays(zip);
  say(`Arrives in about ${days} days.`, "ok");
}

document.addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  say("", "");
  if (b.dataset.add) { addItem(cart, b.dataset.add, 1); renderCart(); }
  if (b.id === "checkout") checkout();
  if (b.id === "ship") estimateShipping();
  if (b.id === "clear") { cart = {}; renderCart(); document.getElementById("receipt").innerHTML = ""; }
});

// If something crashes, tell the shopper, and let the error keep going so the watcher sees it.
window.addEventListener("error", () => say("Something went wrong. It's been reported automatically.", "bad"));

renderCart();

// "You might also like": comes from a slow API on purpose, so the speed check has something to find.
fetch("/api/demo/recommendations")
  .then((r) => r.json())
  .then((list) => { document.getElementById("recs").innerHTML = "<b>You might also like:</b> " + list.join(", "); })
  .catch(() => { document.getElementById("recs").textContent = ""; });
