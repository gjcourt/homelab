// Custom JS — overridden per environment if needed.
//
// Tile-click beacon. Homepage tiles are plain <a href> links, so nothing is
// recorded when one is clicked. This delegated listener fires a fire-and-forget
// navigator.sendBeacon() to the same-origin /api/clicks endpoint on every tile
// click, letting the homepage-clicks exporter increment
// homepage_tile_clicks_total{service,group} for Grafana. See
// apps/base/homepage-clicks/ and images/homepage-clicks/.
//
// Robustness: it keys off Homepage's stable custom classes
// (.service-title-text / .service-name / .service-group-name), not layout
// classes, so it survives theme + tab changes. It is best-effort: any failure
// is swallowed so navigation is never blocked, and it does nothing if
// sendBeacon is unavailable.
(function () {
  "use strict";

  if (!("sendBeacon" in navigator)) {
    return;
  }

  var BEACON_URL = "/api/clicks";
  var MAX_LEN = 64;

  function tileName(anchor) {
    // .service-name holds the tile title AND a nested .service-description <p>.
    // Clone it, drop the description, and read what's left.
    var nameEl = anchor.querySelector(".service-name");
    if (!nameEl) {
      return "";
    }
    var clone = nameEl.cloneNode(true);
    var desc = clone.querySelector(".service-description");
    if (desc) {
      desc.parentNode.removeChild(desc);
    }
    return (clone.textContent || "").trim().slice(0, MAX_LEN);
  }

  function groupName(anchor) {
    var groupEl = anchor.closest(".services-group");
    if (!groupEl) {
      return "";
    }
    var h = groupEl.querySelector(".service-group-name");
    return h ? (h.textContent || "").trim().slice(0, MAX_LEN) : "";
  }

  // Capture phase so the beacon is queued before the browser starts navigating
  // away (which can otherwise cancel a bubbling-phase handler).
  document.addEventListener(
    "click",
    function (event) {
      try {
        var target = event.target;
        if (!target || !target.closest) {
          return;
        }
        // Only service tiles carry .service-title-text; bookmarks and widgets
        // don't, so they're ignored.
        var anchor = target.closest("a.service-title-text");
        if (!anchor) {
          return;
        }
        var service = tileName(anchor);
        if (!service) {
          return;
        }
        var payload = JSON.stringify({ service: service, group: groupName(anchor) });
        // sendBeacon(string) sends text/plain;charset=UTF-8 — a CORS-safelisted
        // "simple" request, so no preflight; the exporter parses the JSON body
        // regardless of content type.
        navigator.sendBeacon(BEACON_URL, payload);
      } catch (err) {
        // Never let tracking interfere with navigation.
      }
    },
    true,
  );
})();
