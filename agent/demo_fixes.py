"""Prepared answers for the demo website's three planted bugs.

Used only when no ANTHROPIC_API_KEY is set, so the crash-to-fix loop can be shown
without a key. The tests and the fix still run for real in the sandbox.
Each entry swaps one function, so the fixes work in any order.
"""

DEMO_FIXES = {
    "applyCoupon": {
        "old": """function applyCoupon(total, code) {
  const coupon = COUPONS[code.trim().toUpperCase()];
  return Math.round(total * (1 - coupon.percent / 100) * 100) / 100;
}""",
        "new": """function applyCoupon(total, code) {
  const coupon = COUPONS[code.trim().toUpperCase()];
  // Unknown codes give no discount instead of crashing checkout
  if (!coupon) return total;
  return Math.round(total * (1 - coupon.percent / 100) * 100) / 100;
}""",
        "answer": {
            "summary": "Checkout crashes when a shopper types a coupon code that doesn't exist, so they can't place the order.",
            "root_cause": "applyCoupon() looks the code up in COUPONS and reads .percent from the result. An unknown code returns undefined, and reading .percent from undefined throws a TypeError that stops checkout.",
            "next_steps": ["Treat unknown codes as no discount instead of crashing.", "Keep the new test with a made-up code.", "Show the shopper that the code isn't valid."],
            "confidence": 94,
            "test_code": """function test_unknown_coupon_gives_no_discount() {
  assert(applyCoupon(50, "NOTREAL") === 50, "an unknown code should leave the total unchanged");
}

function test_real_coupon_still_works() {
  assert(applyCoupon(50, "save10") === 45, "SAVE10 should take 10% off");
}
""",
            "pr_title": "Ignore unknown coupon codes instead of crashing checkout",
            "fix_plain": "If the coupon code isn't real, the page now just skips the discount instead of breaking.",
        },
    },
    "cheapestItem": {
        "old": """function cheapestItem(cart) {
  const skus = Object.keys(cart).sort((a, b) => PRODUCTS[a].price - PRODUCTS[b].price);
  return PRODUCTS[skus[0]].name;
}""",
        "new": """function cheapestItem(cart) {
  const skus = Object.keys(cart).sort((a, b) => PRODUCTS[a].price - PRODUCTS[b].price);
  // An empty cart has no cheapest item
  if (skus.length === 0) return "None";
  return PRODUCTS[skus[0]].name;
}""",
        "answer": {
            "summary": "Checking out with an empty cart crashes the page instead of showing a $0 order.",
            "root_cause": "cheapestItem() sorts the cart and reads PRODUCTS[skus[0]].name. With an empty cart, skus[0] is undefined, PRODUCTS[undefined] is undefined, and reading .name from it throws a TypeError.",
            "next_steps": ["Return a placeholder like \"None\" when the cart is empty.", "Keep the new test for an empty cart.", "Consider disabling the Check out button while the cart is empty."],
            "confidence": 95,
            "test_code": """function test_empty_cart_has_no_cheapest_item() {
  assert(cheapestItem({}) === "None", "an empty cart should say None");
}

function test_cheapest_item_in_a_normal_cart() {
  assert(cheapestItem({ tee: 1, mug: 2 }) === "Coffee mug", "the mug is the cheapest");
}
""",
            "pr_title": "Handle an empty cart in cheapestItem()",
            "fix_plain": "If the cart is empty, the page now says \"None\" instead of looking for an item that isn't there.",
        },
    },
    "shippingDays": {
        "old": """function shippingDays(zip) {
  return SHIPPING_ZONES[zip.trim()[0]].days;
}""",
        "new": """function shippingDays(zip) {
  const zone = SHIPPING_ZONES[String(zip || "").trim()[0]];
  // Blank or non-US ZIP codes get the slowest estimate instead of a crash
  return zone ? zone.days : 5;
}""",
        "answer": {
            "summary": "Estimating shipping crashes when the ZIP box is empty or starts with a letter.",
            "root_cause": "shippingDays() takes the first character of the ZIP code and looks up its zone. An empty box has no first character, so the lookup returns undefined, and reading .days from undefined throws a TypeError.",
            "next_steps": ["Fall back to the slowest estimate when the ZIP code isn't usable.", "Keep the new test for a blank ZIP code.", "Ask the shopper to enter a 5-digit ZIP before estimating."],
            "confidence": 93,
            "test_code": """function test_blank_zip_does_not_crash() {
  assert(shippingDays("") === 5, "a blank ZIP should fall back to 5 days");
}

function test_known_zip_still_works() {
  assert(shippingDays("60622") === 2, "60622 is in zone 6, which ships in 2 days");
}
""",
            "pr_title": "Fall back to a default when the ZIP code can't be used",
            "fix_plain": "If the ZIP box is empty or wrong, the page now gives a normal 5-day estimate instead of breaking.",
        },
    },
}


def demo_answer_for(func: str, source: str) -> dict | None:
    fix = DEMO_FIXES.get(func)
    if not fix or fix["old"] not in source:
        return None
    return dict(fix["answer"], category="code_bug", test_setup="", fixed_source=source.replace(fix["old"], fix["new"]))


# ---- Lakeview Hotel (booking demo) and Hall 1 Power (data center dashboard demo) ----

def _fix(old, new, summary, root, steps, plain, tests, title, conf=92):
    return {"old": old, "new": new, "answer": {"summary": summary, "root_cause": root, "next_steps": steps,
            "confidence": conf, "test_code": tests, "pr_title": title, "fix_plain": plain}}


DEMO_FIXES.update({
    "priceFor": _fix(
        """function priceFor(roomId) {
  return ROOMS[roomId].price;
}""",
        """function priceFor(roomId) {
  const room = ROOMS[roomId];
  // No room picked yet: there's no price to show
  if (!room) return 0;
  return room.price;
}""",
        "Pressing Get price before choosing a room breaks the page.",
        "priceFor() looks up the room and reads .price. With no room chosen the lookup finds nothing, and reading .price from nothing throws a TypeError.",
        ["Return 0 (or ask the guest to pick a room) when no room is chosen.", "Turn off the Get price button until a room is picked."],
        "If no room is picked, the page shows $0 instead of breaking.",
        """function test_no_room_picked() {
  assert(priceFor("") === 0, "no room should cost 0");
}
function test_real_room_price() {
  assert(priceFor("king") === 189, "the king suite is $189");
}
""", "Handle a missing room in priceFor()"),
    "guestInitials": _fix(
        """function guestInitials(fullName) {
  const parts = fullName.trim().split(" ");
  return parts[0][0].toUpperCase() + parts[1][0].toUpperCase();
}""",
        """function guestInitials(fullName) {
  const parts = fullName.trim().split(/\\s+/).filter(Boolean);
  // Works for empty names and one-word names too
  return parts.slice(0, 2).map((p) => p[0].toUpperCase()).join("");
}""",
        "Making a name badge breaks when the name is empty or only one word, like \"Cher\".",
        "guestInitials() always reads the first letter of the second word. With one word (or none) there is no second word, so it reads from nothing and throws a TypeError.",
        ["Use whatever name parts exist instead of assuming two.", "Ask for a name before making a badge."],
        "The badge now works for any name, even one word, and shows nothing if the box is empty.",
        """function test_one_word_name() {
  assert(guestInitials("Cher") === "C", "one word gives one letter");
}
function test_empty_name() {
  assert(guestInitials("") === "", "an empty name gives no letters");
}
function test_two_word_name() {
  assert(guestInitials("jane smith") === "JS", "two words give two letters");
}
""", "Make guestInitials() work for any name"),
    "freeTimes": _fix(
        """function freeTimes(day) {
  return SLOTS[day.trim().toLowerCase().slice(0, 3)].filter((t) => t !== "12:00");
}""",
        """function freeTimes(day) {
  // Days without spa times (or a typo) give an empty list instead of a crash
  const slots = SLOTS[day.trim().toLowerCase().slice(0, 3)] || [];
  return slots.filter((t) => t !== "12:00");
}""",
        "Showing spa times breaks when the day box is empty, misspelled, or a day with no times.",
        "freeTimes() looks up the day's time slots and calls .filter on them. For an unknown day there are no slots, so it calls .filter on nothing and throws a TypeError.",
        ["Treat unknown days as having no free times.", "Show a message like \"No times that day\" when the list is empty."],
        "If the day isn't known, the page shows no times instead of breaking.",
        """function test_unknown_day() {
  assert(freeTimes("zzz").length === 0, "an unknown day has no times");
}
function test_real_day() {
  assert(freeTimes("Monday").length === 3, "Monday has 3 times");
}
""", "Handle unknown days in freeTimes()"),
    "rackLoad": _fix(
        """function rackLoad(id) {
  const r = RACKS[id];
  return r.kw.toFixed(1) + " kW of " + r.limit + " kW";
}""",
        """function rackLoad(id) {
  const r = RACKS[id];
  // A meter that's offline sends no reading: say so instead of breaking the panel
  if (!r || r.kw == null) return "no reading (meter offline)";
  return r.kw.toFixed(1) + " kW of " + r.limit + " kW";
}""",
        "Opening rack B2 breaks the power panel, because its power meter is offline and sends no reading.",
        "rackLoad() formats the rack's kW reading with .toFixed(). Rack B2's reading is empty (null), and you can't format an empty value, so it throws a TypeError and the panel goes blank.",
        ["Show \"no reading\" when a meter is offline.", "Raise an alarm when a rack's meter stops reporting."],
        "When a power meter is offline, the panel now says \"no reading\" instead of going blank.",
        """function test_offline_meter() {
  assert(rackLoad("B2").includes("no reading"), "B2 should say no reading");
}
function test_normal_rack() {
  assert(rackLoad("A1") === "6.4 kW of 8 kW", "A1 shows its load");
}
""", "Show offline meters instead of crashing in rackLoad()", 95),
    "topAlarm": _fix(
        """function topAlarm(level) {
  return ALARMS.filter((a) => a.level === level)[0].text;
}""",
        """function topAlarm(level) {
  const match = ALARMS.filter((a) => a.level === level)[0];
  // No alarms at this level is good news, not an error
  return match ? match.text : "None right now";
}""",
        "Clicking Critical alarms breaks the panel when there are no critical alarms, which is exactly when everything is fine.",
        "topAlarm() takes the first alarm at that level and reads its text. When there are none, there is no first alarm, so it reads from nothing and throws a TypeError.",
        ["Show \"None right now\" when there are no alarms at that level.", "Keep a test for the no-alarm case."],
        "With no alarms at that level, the panel now says \"None right now\" instead of breaking.",
        """function test_no_critical_alarms() {
  assert(topAlarm("critical") === "None right now", "no critical alarms");
}
function test_warning_alarm() {
  assert(topAlarm("warning").includes("A2"), "the A2 warning shows");
}
""", "Handle an empty alarm list in topAlarm()"),
    "inUnits": _fix(
        """function inUnits(kw, unit) {
  const u = UNITS[unit.trim().toLowerCase()];
  return (kw * u.factor).toFixed(2) + " " + u.label;
}""",
        """function inUnits(kw, unit) {
  // Unknown or empty units fall back to kW
  const u = UNITS[unit.trim().toLowerCase()] || UNITS.kw;
  return (kw * u.factor).toFixed(2) + " " + u.label;
}""",
        "Converting the total load breaks if the unit box is empty or has a typo.",
        "inUnits() looks up the unit and reads its .factor. An unknown unit finds nothing, and reading .factor from nothing throws a TypeError.",
        ["Fall back to kW for unknown units.", "Use a dropdown (kW, MW, W) instead of a text box."],
        "If the unit isn't known, it now shows the load in kW instead of breaking.",
        """function test_unknown_unit() {
  assert(inUnits(13.5, "zzz") === "13.50 kW", "falls back to kW");
}
function test_megawatts() {
  assert(inUnits(13.5, "MW") === "0.01 MW", "converts to MW");
}
""", "Fall back to kW for unknown units in inUnits()"),
})
