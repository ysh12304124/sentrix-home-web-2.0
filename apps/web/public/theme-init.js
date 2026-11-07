// Run before styles load; no network requests and no business state changes.
(function () {
  var localPreview = document.currentScript && document.currentScript.dataset.previewMode === "development"
    && ["localhost", "127.0.0.1", "[::1]"].indexOf(window.location.hostname) !== -1
    && ["4274", "4275"].indexOf(window.location.port) !== -1;
  if (window.location.port !== "8210" && !localPreview) return;
  var theme = "clear";
  try {
    var saved = window.localStorage.getItem("sentrix.ui.theme.v1");
    if (saved === "clear" || saved === "warm" || saved === "bloom") theme = saved;
  } catch { /* A temporary choice is still available without storage. */ }
  document.documentElement.setAttribute("data-ui-theme", theme);
})();
