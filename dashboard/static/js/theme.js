(function() {
  const saved = localStorage.getItem("pce_theme") || "dark";
  document.documentElement.dataset.theme = saved;
  document.getElementById("themeToggle")?.addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme;
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("pce_theme", next);
    if (typeof loadCharts === "function") loadCharts();
  });
})();