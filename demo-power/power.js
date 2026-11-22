// Data for the demo data center power dashboard. A few bugs are left in on purpose for DebugPilot to catch.
const RACKS = {
  A1: { kw: 6.4, limit: 8 },
  A2: { kw: 7.1, limit: 8 },
  B2: { kw: null, limit: 10 },   // its power meter is offline, so there is no reading
};

const ALARMS = [
  { level: "warning", text: "A2 is above 85% of its power limit" },
  { level: "info", text: "Generator test scheduled for Friday" },
];

const UNITS = { kw: { factor: 1, label: "kW" }, mw: { factor: 0.001, label: "MW" }, w: { factor: 1000, label: "W" } };

function rackLoad(id) {
  const r = RACKS[id];
  return r.kw.toFixed(1) + " kW of " + r.limit + " kW";
}

function topAlarm(level) {
  return ALARMS.filter((a) => a.level === level)[0].text;
}

function inUnits(kw, unit) {
  const u = UNITS[unit.trim().toLowerCase()];
  return (kw * u.factor).toFixed(2) + " " + u.label;
}
