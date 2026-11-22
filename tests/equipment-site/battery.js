// The temperature sensor sometimes sends no reading (null).
var reading = { charge: 97, temp: null };
function showTemp(r) {
  return "Battery temperature: " + r.temp.toFixed(1) + " C";
}
document.getElementById("t").textContent = showTemp(reading);
