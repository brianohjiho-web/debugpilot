var TAGS = { red: ["apple", "cherry"], green: ["lime"] };
function lookup(word) {
  return TAGS[word].join(", ");
}
document.getElementById("go").addEventListener("click", function () {
  document.getElementById("out").textContent = lookup(document.getElementById("q").value);
});
