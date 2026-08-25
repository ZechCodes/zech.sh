/* Cal.com inline booking embed for the /book page.
   Booking is the page's purpose, so the embed loads eagerly. */
(function () {
  "use strict";
  var EMBED_SCRIPT = "https://app.cal.com/embed/embed.js";
  var CAL_LINK = "zech-zimmerman/intro";
  var NAMESPACE = "intro";
  var EMBER = "#ff6a2c";
  var LOAD_TIMEOUT_MS = 8000;

  var target = document.getElementById("cal-inline");
  var fallback = document.getElementById("booking-fallback");
  if (!target) return;

  var settled = false;

  function embedRendered() {
    return !!target.querySelector("iframe");
  }

  function showFallback() {
    if (settled || embedRendered()) return;
    settled = true;
    target.hidden = true;
    if (fallback) fallback.hidden = false;
  }

  /* The CSP's per-request style nonce blocks the <style> tags Cal injects into the
     page and into cal-inline's shadow DOM, leaving its loader/error chrome unstyled
     and visible ("Something went wrong." above a working booker). Constructed
     stylesheets are CSSOM, which style-src does not govern, so re-apply each
     blocked style tag that way. */
  var adoptedStyleTags = [];
  var shadowObserved = false;

  function adoptStyles(container, root) {
    container.querySelectorAll("style").forEach(function (tag) {
      if (adoptedStyleTags.indexOf(tag) !== -1) return;
      adoptedStyleTags.push(tag);
      var sheet = new CSSStyleSheet();
      // A malformed vendor sheet should degrade to unstyled chrome, not kill the embed.
      try {
        sheet.replaceSync(tag.textContent);
      } catch (parseError) {
        return;
      }
      root.adoptedStyleSheets = root.adoptedStyleSheets.concat(sheet);
    });
  }

  function syncVendorStyles() {
    if (typeof CSSStyleSheet !== "function" || !("adoptedStyleSheets" in document)) return;
    adoptStyles(target, document);
    var inlineEl = target.querySelector("cal-inline");
    if (inlineEl && inlineEl.shadowRoot) {
      adoptStyles(inlineEl.shadowRoot, inlineEl.shadowRoot);
      if (!shadowObserved) {
        shadowObserved = true;
        new MutationObserver(syncVendorStyles).observe(inlineEl.shadowRoot, {
          childList: true,
          subtree: true,
        });
      }
    }
  }

  // A blocked/failed vendor script fires an error event that only capture-phase listeners see.
  addEventListener(
    "error",
    function (e) {
      var el = e.target;
      if (el && el.tagName === "SCRIPT" && el.src === EMBED_SCRIPT) showFallback();
    },
    true
  );

  // Cal's official snippet: queue calls on window.Cal, the vendor script drains the queue on load.
  (function (C, A, L) {
    var p = function (a, ar) {
      a.q.push(ar);
    };
    var d = C.document;
    C.Cal =
      C.Cal ||
      function () {
        var cal = C.Cal;
        var ar = arguments;
        if (!cal.loaded) {
          cal.ns = {};
          cal.q = cal.q || [];
          d.head.appendChild(d.createElement("script")).src = A;
          cal.loaded = true;
        }
        if (ar[0] === L) {
          var api = function () {
            p(api, arguments);
          };
          var namespace = ar[1];
          api.q = api.q || [];
          if (typeof namespace === "string") {
            cal.ns[namespace] = cal.ns[namespace] || api;
            p(cal.ns[namespace], ar);
            p(cal, ["initNamespace", namespace]);
          } else p(cal, ar);
          return;
        }
        p(cal, ar);
      };
  })(window, EMBED_SCRIPT, "init");

  var Cal = window.Cal;
  Cal("init", NAMESPACE, { origin: "https://app.cal.com" });
  Cal.ns[NAMESPACE]("inline", {
    elementOrSelector: "#cal-inline",
    calLink: CAL_LINK,
    config: { layout: "month_view", theme: "dark" },
  });
  Cal.ns[NAMESPACE]("ui", {
    theme: "dark",
    layout: "month_view",
    hideEventTypeDetails: false,
    cssVarsPerTheme: {
      light: { "cal-brand": EMBER },
      dark: {
        "cal-brand": EMBER,
        "cal-bg": "#0c1018",
        "cal-bg-emphasis": "#1b2432",
        "cal-bg-subtle": "#131a26",
        "cal-bg-muted": "#101724",
        "cal-border": "#232c3b",
        "cal-border-subtle": "#1a2230",
        "cal-border-emphasis": "#313c4d",
        "cal-text": "#f4efe7",
        "cal-text-emphasis": "#ffffff",
        "cal-text-subtle": "#b9b3a8",
        "cal-text-muted": "#8a8579",
      },
    },
  });

  new MutationObserver(syncVendorStyles).observe(target, { childList: true, subtree: true });
  syncVendorStyles();
  setTimeout(function () {
    if (embedRendered()) settled = true;
    else showFallback();
  }, LOAD_TIMEOUT_MS);
})();
