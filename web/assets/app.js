/* aiOS — interactions du site liquid glass
   BSD-3-Clause */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------- thème --- */
  var KEY = "aios-theme";
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    var b = $("#theme-toggle");
    if (b) b.textContent = t === "light" ? "☀" : "☾";
    try { localStorage.setItem(KEY, t); } catch (e) {}
  }
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  applyTheme(saved || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  var tb = $("#theme-toggle");
  if (tb) tb.addEventListener("click", function () {
    applyTheme(document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light");
  });

  /* --------------------------------------------------------- rail ----- */
  var rail = $("#rail");
  var menu = $("#menu-toggle");
  if (menu && rail) menu.addEventListener("click", function () { rail.classList.toggle("open"); });
  document.addEventListener("click", function (e) {
    if (!rail || !menu) return;
    if (rail.classList.contains("open") && !rail.contains(e.target) && !menu.contains(e.target)) {
      rail.classList.remove("open");
    }
  });

  /* ------------------------------------------------- recherche ⌘K ----- */
  var overlay = $("#search-overlay");
  var input = $("#search-input");
  var results = $("#search-results");
  var index = null;
  var loading = false;

  function loadIndex() {
    if (index || loading) return;
    loading = true;
    fetch("search-index.json")
      .then(function (r) { return r.json(); })
      .then(function (d) { index = d; loading = false; })
      .catch(function () { index = []; loading = false; });
  }

  function openSearch() {
    if (!overlay) return;
    overlay.classList.add("open");
    loadIndex();
    setTimeout(function () { input && input.focus(); }, 30);
    document.body.style.overflow = "hidden";
  }
  function closeSearch() {
    if (!overlay) return;
    overlay.classList.remove("open");
    document.body.style.overflow = "";
  }

  var sb = $("#search-btn");
  if (sb) sb.addEventListener("click", openSearch);
  if (overlay) overlay.addEventListener("click", function (e) {
    if (e.target === overlay) closeSearch();
  });

  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); }
    if (e.key === "Escape") { closeSearch(); closeLightbox(); }
  });

  function score(q, item) {
    var t = item.title.toLowerCase(), h = (item.headings || []).join(" ").toLowerCase();
    var b = (item.body || "").toLowerCase();
    if (t === q) return 100;
    if (t.indexOf(q) === 0) return 80;
    if (t.indexOf(q) > -1) return 60;
    if (h.indexOf(q) > -1) return 40;
    if (b.indexOf(q) > -1) return 15;
    return 0;
  }

  function render(q) {
    if (!results) return;
    q = q.trim().toLowerCase();
    if (!q) {
      results.innerHTML = '<div class="empty">Tape une commande, un module, une règle…</div>';
      return;
    }
    if (!index) { results.innerHTML = '<div class="empty">Chargement de l’index…</div>'; return; }
    var hits = index
      .map(function (it) { return { it: it, s: score(q, it) }; })
      .filter(function (x) { return x.s > 0; })
      .sort(function (a, b) { return b.s - a.s; })
      .slice(0, 8);
    if (!hits.length) {
      results.innerHTML = '<div class="empty">Aucun résultat pour « ' + q.replace(/</g, "&lt;") + ' »</div>';
      return;
    }
    results.innerHTML = hits.map(function (x, i) {
      return '<a href="' + x.it.url + '"' + (i === 0 ? ' class="sel"' : "") + '>' +
        "<b>" + x.it.title + "</b><span>" + (x.it.snippet || "") + "</span></a>";
    }).join("");
  }

  if (input) {
    input.addEventListener("input", function () { render(input.value); });
    input.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;
      var sel = $(".results a.sel") || $(".results a");
      if (sel) { e.preventDefault(); window.location.href = sel.getAttribute("href"); }
    });
  }

  /* ------------------------------------------------------- lightbox --- */
  var lb = $("#lightbox");
  var lbImg = lb && lb.querySelector("img");

  function openLightbox(src, alt) {
    if (!lb) return;
    lbImg.src = src;
    lbImg.alt = alt || "";
    lb.classList.add("open");
    document.body.style.overflow = "hidden";
  }
  function closeLightbox() {
    if (!lb) return;
    lb.classList.remove("open");
    document.body.style.overflow = "";
  }
  $$("img").forEach(function (img) {
    var inShot = img.closest(".shot") || img.closest("figure");
    if (!inShot) return;
    img.addEventListener("click", function () { openLightbox(img.currentSrc || img.src, img.alt); });
  });
  if (lb) lb.addEventListener("click", function (e) {
    if (e.target === lb || e.target.classList.contains("close")) closeLightbox();
  });

  /* ------------------------------------------------------ diagrammes --- */
  var diagramNodes = $$(".diagram");

  function toggleSource(box) {
    var pre = box.querySelector("pre.source");
    if (!pre) return;
    if (!pre.textContent) pre.textContent = box.querySelector(".mermaid").textContent;
    box.classList.toggle("show-source");
    var btn = box.querySelector("[data-act=src]");
    if (btn) btn.textContent = box.classList.contains("show-source") ? "Masquer la source" : "Voir la source";
  }

  diagramNodes.forEach(function (box) {
    var tools = box.querySelector(".dtools");
    if (tools) {
      tools.addEventListener("click", function (e) {
        var act = e.target.getAttribute("data-act");
        if (act === "src") toggleSource(box);
        if (act === "dl") downloadSvg(box);
      });
    }
  });

  function downloadSvg(box) {
    var svg = box.querySelector("svg");
    if (!svg) return;
    var blob = new Blob(['<?xml version="1.0" encoding="UTF-8"?>\n' + svg.outerHTML],
      { type: "image/svg+xml" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (document.title.replace(/[^\w-]+/g, "-").toLowerCase() || "diagramme") + ".svg";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  function renderDiagrams() {
    if (typeof mermaid === "undefined") {
      $$(".diagram .mermaid").forEach(function (n) {
        if (!n.querySelector("svg") && !n.querySelector(".diagram-fallback")) {
          var d = document.createElement("div");
          d.className = "diagram-fallback";
          d.textContent = "Diagramme Mermaid non chargé — le code source reste disponible ci-dessous.";
          n.after(d);
        }
      });
      return;
    }
    try {
      mermaid.initialize({
        startOnLoad: false,
        theme: document.documentElement.getAttribute("data-theme") === "light" ? "neutral" : "dark",
        themeVariables: {
          primaryColor: "#7c8cff",
          primaryTextColor: "#eef1f8",
          primaryBorderColor: "rgba(255,255,255,.35)",
          lineColor: "#37e6d4",
          secondaryColor: "rgba(124,140,255,.22)",
          tertiaryColor: "rgba(55,230,212,.14)",
          background: "transparent",
          mainBkg: "rgba(124,140,255,.16)",
          nodeBorder: "#7c8cff",
          clusterBkg: "rgba(255,255,255,.06)",
          clusterBorder: "rgba(255,255,255,.18)",
          titleColor: "#eef1f8",
          edgeLabelBackground: "rgba(4,6,14,.72)",
          fontSize: "14px"
        },
        securityLevel: "strict",
        flowchart: { curve: "basis", htmlLabels: true },
        fontFamily: "-apple-system, BlinkMacSystemFont, Inter, sans-serif"
      });
      mermaid.run({ nodes: $$(".diagram .mermaid") });
    } catch (err) {
      console.warn("Mermaid:", err);
    }
  }

  if (typeof mermaid !== "undefined") {
    renderDiagrams();
  } else {
    window.addEventListener("load", renderDiagrams);
  }

  /* ----------------------------------------------------------- tilt --- */
  if (!reduced && window.matchMedia("(hover: hover)").matches) {
    $$(".card").forEach(function (card) {
      card.addEventListener("mousemove", function (e) {
        var r = card.getBoundingClientRect();
        var x = (e.clientX - r.left) / r.width - 0.5;
        var y = (e.clientY - r.top) / r.height - 0.5;
        card.style.transform =
          "perspective(900px) rotateX(" + (-y * 7).toFixed(2) + "deg) rotateY(" +
          (x * 8).toFixed(2) + "deg) translateY(-4px)";
      });
      card.addEventListener("mouseleave", function () { card.style.transform = ""; });
    });
  }

  /* --------------------------------------------------- révélation ----- */
  if (!reduced && "IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.style.opacity = "1";
          en.target.style.transform = "none";
          io.unobserve(en.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    $$(".card, .shot, .stat").forEach(function (el, i) {
      el.style.opacity = "0";
      el.style.transform = "translateY(18px)";
      el.style.transition = "opacity .6s " + Math.min(i * 45, 360) + "ms " +
        "cubic-bezier(.22,1,.36,1), transform .6s " + Math.min(i * 45, 360) +
        "ms cubic-bezier(.22,1,.36,1)";
      io.observe(el);
    });
  }
})();
