/* О!дин запрос — виджет чата для страницы партнёра (#435).
 * <script src="http://localhost:8080/embed.js" data-partner="my-shop" async></script>
 * Атрибуты: data-partner — метка партнёра в адресе iframe (?partner=);
 * data-label — текст кнопки; data-open="true" — открыть панель сразу.
 * Чат — iframe приложения с того же адреса, что и скрипт. Стили страницы не меняются. */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script || window.__odinZaprosEmbed) return;
  window.__odinZaprosEmbed = true;
  var base = new URL(script.src, location.href);
  var app = new URL("/", base.origin);
  var partner = script.getAttribute("data-partner");
  if (partner) app.searchParams.set("partner", partner);
  var label = script.getAttribute("data-label") || "Спланировать поездку";
  var YELLOW = "#FFD800", INK = "#141414";

  var button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-controls", "odin-zapros-panel");
  button.style.cssText =
    "position:fixed;right:20px;bottom:20px;z-index:2147483646;padding:14px 20px;" +
    "border:2px solid " + INK + ";border-radius:999px;background:" + YELLOW + ";color:" + INK +
    ";font:600 16px/1.2 system-ui,sans-serif;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.2)";

  var panel = document.createElement("div");
  panel.id = "odin-zapros-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "О!дин запрос — агент путешествий");
  panel.style.cssText =
    "position:fixed;right:20px;bottom:84px;z-index:2147483647;display:none;" +
    "width:min(420px,calc(100vw - 40px));height:min(680px,calc(100vh - 104px));" +
    "border:2px solid " + INK + ";border-radius:16px;overflow:hidden;background:#fff;" +
    "box-shadow:0 8px 32px rgba(0,0,0,.25)";

  var bar = document.createElement("div");
  bar.style.cssText =
    "display:flex;align-items:center;justify-content:space-between;height:40px;padding:0 12px;" +
    "background:" + YELLOW + ";color:" + INK + ";font:700 15px/1 system-ui,sans-serif";
  bar.appendChild(document.createTextNode("О!дин запрос"));
  var close = document.createElement("button");
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", "Закрыть");
  close.style.cssText =
    "border:0;background:transparent;color:" + INK + ";font:700 22px/1 system-ui;cursor:pointer";
  bar.appendChild(close);

  var frame = null; // the app loads on the first open, not with the partner page
  function toggle(open) {
    if (open && !frame) {
      frame = document.createElement("iframe");
      frame.src = app.href;
      frame.title = "О!дин запрос — агент путешествий";
      frame.allow = "clipboard-write";
      frame.style.cssText = "display:block;width:100%;height:calc(100% - 40px);border:0";
      panel.appendChild(frame);
    }
    panel.style.display = open ? "block" : "none";
    button.setAttribute("aria-expanded", String(open));
  }
  button.addEventListener("click", function () {
    toggle(panel.style.display === "none");
  });
  close.addEventListener("click", function () {
    toggle(false);
  });

  panel.appendChild(bar);
  function mount() {
    document.body.appendChild(panel);
    document.body.appendChild(button);
    if (script.getAttribute("data-open") === "true") toggle(true);
  }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
