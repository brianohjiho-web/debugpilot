// Cart logic for the demo shop. Plain functions, no page code, so each one can be tested on its own.
// A few bugs are left in on purpose so DebugPilot has something real to catch.

const PRODUCTS = {
  mug: { name: "Coffee mug", price: 12 },
  tee: { name: "T-shirt", price: 24 },
  cap: { name: "Cap", price: 18 },
};

const COUPONS = {
  SAVE10: { percent: 10 },
  WELCOME20: { percent: 20 },
};

const SHIPPING_ZONES = {
  "0": { days: 5 }, "1": { days: 4 }, "2": { days: 4 }, "3": { days: 3 }, "4": { days: 3 },
  "5": { days: 3 }, "6": { days: 2 }, "7": { days: 3 }, "8": { days: 4 }, "9": { days: 5 },
};

function addItem(cart, sku, qty) {
  cart[sku] = (cart[sku] || 0) + qty;
  return cart;
}

function subtotal(cart) {
  return Object.entries(cart).reduce((sum, [sku, qty]) => sum + PRODUCTS[sku].price * qty, 0);
}

function applyCoupon(total, code) {
  const coupon = COUPONS[code.trim().toUpperCase()];
  return Math.round(total * (1 - coupon.percent / 100) * 100) / 100;
}

function cheapestItem(cart) {
  const skus = Object.keys(cart).sort((a, b) => PRODUCTS[a].price - PRODUCTS[b].price);
  return PRODUCTS[skus[0]].name;
}

function shippingDays(zip) {
  return SHIPPING_ZONES[zip.trim()[0]].days;
}
