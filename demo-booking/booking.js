// Booking logic for the demo hotel. A few bugs are left in on purpose so DebugPilot has something to catch.
const ROOMS = {
  queen: { name: "Queen room", price: 129 },
  king: { name: "King suite", price: 189 },
};

const SLOTS = {
  mon: ["9:00", "11:00", "15:00"],
  tue: ["10:00", "14:00"],
  fri: ["9:00", "16:00"],
};

function priceFor(roomId) {
  return ROOMS[roomId].price;
}

function guestInitials(fullName) {
  const parts = fullName.trim().split(" ");
  return parts[0][0].toUpperCase() + parts[1][0].toUpperCase();
}

function freeTimes(day) {
  return SLOTS[day.trim().toLowerCase().slice(0, 3)].filter((t) => t !== "12:00");
}
