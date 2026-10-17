(function () {
  try {
    var t = localStorage.getItem("epp-theme");
    if (t === "dark") document.documentElement.setAttribute("data-theme", "dark");
  } catch (e) {
    /* sin localStorage: se queda el tema claro */
  }
})();
