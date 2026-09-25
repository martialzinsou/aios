/* aiOS — console locale (verre liquide)
   BSD-3-Clause */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var params = new URLSearchParams(location.search);
  var token = params.get("t") || sessionStorage.getItem("aios-token") || "";
  if (token) {
    sessionStorage.setItem("aios-token", token);
    params.delete("t");
    var clean = location.pathname + (params.toString() ? "?" + params.toString() : "") + location.hash;
    history.replaceState(null, "", clean);
  }

  var state = null;
  var busy = false;
  var route = (location.hash || "#/apercu").replace(/^#\/?/, "") || "apercu";

  var esc = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };
  var nl2br = function (s) { return esc(s).replace(/\n/g, "<br>"); };

  /* ------------------------------------------------------------ thème -- */
  var KEY = "aios-ui-theme";
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    $("#theme").textContent = t === "light" ? "☀" : "☾";
    try { localStorage.setItem(KEY, t); } catch (e) {}
  }
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  applyTheme(saved || "dark");
  $("#theme").addEventListener("click", function () {
    applyTheme(document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light");
  });

  $("#menu").addEventListener("click", function () { $("#rail").classList.toggle("open"); });

  /* ------------------------------------------------------------ horloge -- */
  function tick() {
    var d = new Date(), p = function (n) { return (n < 10 ? "0" : "") + n; };
    var el = $("#clock");
    if (el) el.textContent = p(d.getHours()) + ":" + p(d.getMinutes());
  }
  tick();
  setInterval(tick, 15000);

  /* ----------------------------------------------------------- réseau -- */
  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers, { "X-AIOS-Token": token });
    return fetch(path, opts).then(function (r) {
      if (r.status === 401) {
        $("#status-pill").className = "pill dead";
        $("#status-pill").innerHTML = "<i></i>jeton refusé";
        throw new Error("401");
      }
      return r.json();
    });
  }

  function load() {
    api("/api/state").then(function (s) {
      state = s;
      var run = s.run && s.run.status === "running";
      var pill = $("#status-pill");
      if (run) {
        pill.className = "pill busy";
        pill.innerHTML = "<i></i>exécution en cours";
      } else {
        pill.className = "pill";
        pill.innerHTML = "<i></i>agent prêt · " + esc(s.doctor.brain) +
          (s.pending.length ? " · " + s.pending.length + " en attente" : "");
      }
      render();
      modal();
    }).catch(function () { /* prochain tick */ });
  }

  /* ------------------------------------------------------------ routes -- */
  function setRoute() {
    route = (location.hash || "#/apercu").replace(/^#\/?/, "") || "apercu";
    document.querySelectorAll("[data-route]").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-route") === route);
    });
    render();
  }
  window.addEventListener("hashchange", setRoute);

  /* --------------------------------------------------------- fragments -- */
  function head(eyebrow, title, sub) {
    return '<div class="head"><div><span class="eyebrow"><i></i>' + eyebrow +
      "</span><h1>" + title + "</h1><p>" + sub + "</p></div></div>";
  }

  function statBox(value, label, cls) {
    return '<div class="stat glass ' + (cls || "") + '"><b>' + esc(value) +
      "</b><span>" + esc(label) + "</span></div>";
  }

  function verdictBadge(verdict) {
    var v = String(verdict || "").toLowerCase();
    var cls = v.indexOf("deny") === 0 ? "deny" : v.indexOf("confirm") === 0 ? "confirm" : "allow";
    return '<span class="badge ' + cls + '">' + esc(String(verdict || "ALLOW")) + "</span>";
  }

  function riskBadge(risk) {
    var r = String(risk || "READ").toUpperCase();
    var cls = r === "PRIVILEGED" || r === "DESTRUCTIVE" ? "deny"
      : r === "EXECUTE" || r === "WRITE" ? "confirm" : "risk";
    return '<span class="badge ' + cls + '">' + esc(r) + "</span>";
  }

  function timeOf(ts) {
    if (typeof ts === "string" && ts.indexOf("T") > 0) {
      return ts.slice(0, 10) + " " + ts.slice(11, 19);
    }
    var d = new Date(typeof ts === "number" ? ts * 1000 : ts);
    if (isNaN(d.getTime())) return esc(String(ts));
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
      " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  /* ------------------------------------------------------------ vues ---- */

  function viewApercu(s) {
    var d = s.doctor, st = s.stats;
    return head("Bureau", "Le système, en un coup d'œil",
        "Uptime " + esc((s.server && s.server.uptime) || 0) +
        " s · moindre privilège, confirmation humaine obligatoire, aucune sortie réseau.") +
      '<div class="grid g3">' +
        statBox(st.events, "événements d'audit") +
        statBox(st.allow, "verdicts ALLOW") +
        statBox(st.confirm, "verdicts CONFIRM", "confirm") +
        statBox(st.deny, "verdicts DENY", "deny") +
        statBox(s.tools.length, "outils exposés") +
        statBox(s.audit.intact ? "intacte" : "ALTÉRÉE", "chaîne d'audit") +
      "</div>" +
      '<div class="grid g2">' +
        '<section class="panel glass"><h2>Santé</h2>' +
          '<dl class="kv">' +
            "<dt>Version</dt><dd class=\"mono\">" + esc(d.version) + "</dd>" +
            "<dt>Python</dt><dd class=\"mono\">" + esc(d.python) + "</dd>" +
            "<dt>Cerveau</dt><dd>" + esc(d.brain) + (d.model ? " · <span class=\"mono\">" + esc(d.model) + "</span>" : "") + "</dd>" +
            "<dt>Jail</dt><dd class=\"mono\">" + esc((d.jail || []).join(":")) + "</dd>" +
            "<dt>Politique</dt><dd class=\"mono\">" + esc(d.policy_source) + "</dd>" +
            "<dt>Budget</dt><dd>" + d.max_steps + " étapes · " + d.max_seconds + " s</dd>" +
            "<dt>Confirmations</dt><dd>" + (d.confirm ? "obligatoires (I2)" : "désactivées") + "</dd>" +
          "</dl></section>" +
        '<section class="panel glass"><h2>Accréditations actives</h2>' +
          '<p class="hint">Chaque ligne correspond à un « oui » humain antérieur, à durée limitée.</p>' +
          (s.grants.length ? grantsTable(s.grants)
            : '<div class="empty">Aucune accréditation en cours — l\'agent repasse par une confirmation pour toute action non-lecture.</div>') +
        "</section>" +
      "</div>";
  }

  function grantsTable(grants) {
    return '<div class="scroll"><table><thead><tr><th>Action</th><th>Cible</th>' +
      "<th>Accordée par</th><th>TTL</th></tr></thead><tbody>" +
      grants.map(function (g) {
        return "<tr><td class=\"mono\">" + esc(g.action) + "</td><td class=\"mono\">" +
          esc(g.target) + "</td><td>" + esc(g.approved_by) + "</td><td>" +
          Math.round(g.ttl) + " s</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function viewConversation(s) {
    var msgs = s.conversation || [];
    var html = head("Conversation", "Dialogue avec l'agent",
        "Chaque appel d'outil repasse par la politique : lecture autorisée, écriture confirmée, privilège refusé.") +
      '<section class="panel glass"><div class="chat">';
    if (!msgs.length) {
      html += '<div class="empty">Aucun échange pour l\'instant — pose un objectif ci-dessous.</div>';
    }
    msgs.forEach(function (m) {
      html += '<div class="msg ' + (m.role === "user" ? "user" : "agent") + '">' +
        nl2br(m.content) + "</div>";
    });
    if (s.run && s.run.status && s.run.status !== "running") {
      html += renderRun(s.run);
    } else if (s.run && s.run.status === "running") {
      html += '<div class="msg agent">Exécution en cours…<div class="meta">' +
        esc(s.run.goal) + "</div></div>";
    }
    html += '</div></section>' +
      '<form class="composer glass" id="composer">' +
        '<input id="goal" placeholder="Ex. : liste le dossier /home/chronos/user" autocomplete="off">' +
        '<button class="btn primary" id="send" type="submit">Exécuter</button>' +
      "</form>";
    return html;
  }

  function renderRun(run) {
    var cls = run.status === "answered" ? "" : " deny";
    var h = '<div class="msg agent"><b>' + esc(run.answer || run.status) + "</b>" +
      '<div class="meta">' + esc(run.status) + " · " + (run.duration || 0) +
      " s · cerveau " + esc(run.brain || "—") +
      (run.blocked ? " · " + run.blocked + " appel(s) bloqué(s)" : "") + "</div>";
    if (run.steps && run.steps.length) {
      h += '<div class="steps">' + run.steps.map(function (st) {
        return '<div class="step"><span class="i">' + st.i + "</span>" +
          '<span class="' + (st.ok ? "ok" : "ko") + '">' + (st.ok ? "✓" : "✗") + "</span>" +
          esc(st.action) + (st.tool ? " · " + esc(st.tool) : "") +
          '<span class="out">' + esc((st.output || "").split("\n")[0]) + "</span></div>";
      }).join("") + "</div>";
    }
    return h + "</div>";
  }

  function viewOutils(s) {
    var verdicts = {};
    var rules = (s.policy && s.policy.rules) || [];
    rules.forEach(function (r) {
      var names = String(r.action || "").split(/[\s,]+/).filter(Boolean);
      names.forEach(function (n) { if (!verdicts[n]) verdicts[n] = r.decision; });
    });
    return head("Outils", "Neuf portes, une seule poignée",
        "Tout appel traverse <code>ToolRegistry.call</code> : le risque déclaré ne peut être abaissé, et le verdict est calculé avant toute exécution.") +
      '<div class="tools">' +
      s.tools.map(function (t) {
        var props = (t.parameters && t.parameters.properties) || {};
        var v = verdicts[t.name] || (t.risk === "PRIVILEGED" ? "DENY" : "CONFIRM");
        return '<article class="tool glass"><div class="row">' + riskBadge(t.risk) +
          verdictBadge(v) + "</div><h3>" + esc(t.name) + "</h3><p>" +
          esc(t.description || "") + "</p>" +
          (Object.keys(props).length
            ? '<div class="args">args: ' + Object.keys(props).join(", ") + "</div>"
            : "") + "</article>";
      }).join("") + "</div>";
  }

  function viewPolitique(s) {
    var p = s.policy || {};
    var rules = p.rules || [];
    return head("Politique", "La règle avant l'agent",
        "Première correspondance gagne. Un <code>DENY</code> est rendu avant même de demander à l'humain — jamais habillé par un prompt.") +
      '<section class="panel glass"><h2>Règles (' + rules.length + ")</h2>" +
      '<p class="hint">Fichier : <span class="mono">' +
        esc((s.doctor || {}).policy_source || "(défaut)") + "</span></p>" +
      '<div class="scroll"><table><thead><tr><th>Réf</th><th>Action</th><th>Cible</th>' +
      "<th>Verdict</th><th>Raison</th></tr></thead><tbody>" +
      rules.map(function (r, i) {
        return "<tr><td class=\"mono\">" + esc(r.id || "#" + (i + 1)) +
          "</td><td class=\"mono\">" + esc(r.action || "—") +
          (r.risk ? " <span class=\"badge risk\">" + esc(r.risk) + "</span>" : "") +
          "</td><td class=\"mono\">" + esc(r.target || "—") + "</td><td>" +
          verdictBadge(r.decision) + "</td><td>" + esc(r.reason || "") +
          "</td></tr>";
      }).join("") + "</tbody></table></div></section>";
  }

  function viewJournal(s) {
    var a = s.audit || { records: [], total: 0, intact: true };
    return head("Journal", "Chaîne d'audit inaltérable",
        "Chaque enregistrement est scellé par le hash du précédent (SHA-256) et numéroté : suppression ou réordonnancement détectables.") +
      '<section class="panel glass"><div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:18px">' +
        '<span class="integrity' + (a.intact ? "" : " bad") + '">' +
        (a.intact ? "✓ chaîne intègre" : "✗ chaîne ALTÉRÉE") + "</span>" +
        '<span class="badge">' + a.total + " enregistrements</span>" +
      "</div>" +
      (a.records.length
        ? '<div class="chain">' + a.records.slice().reverse().map(function (r) {
            return '<div class="rec"><span class="seq">#' + esc(r.seq) +
              '</span><span class="ts">' + esc(timeOf(r.ts)) +
              '</span><span class="ev"><b>' + esc(r.event) + "</b> · " +
              esc(r.action || "—") + (r.outcome ? " <span>" + esc(r.outcome) + "</span>" : "") +
              '</span><span class="hash">' + esc(String(r.hash || "").slice(0, 16)) +
              "</span></div>";
          }).join("") + "</div>"
        : '<div class="empty">Journal vide.</div>') +
      "</section>";
  }

  function viewSante(s) {
    var d = s.doctor;
    return head("Santé", "Diagnostic",
        "Le même diagnostic que <code>aios doctor</code>, en permanence.") +
      '<section class="panel glass"><dl class="kv">' +
        "<dt>Version aiOS</dt><dd class=\"mono\">" + esc(d.version) + "</dd>" +
        "<dt>Python</dt><dd class=\"mono\">" + esc(d.python) + "</dd>" +
        "<dt>Cerveau</dt><dd>" + esc(d.brain) + "</dd>" +
        "<dt>Modèle local</dt><dd class=\"mono\">" + esc(d.model || "aucun — repli heuristique") + "</dd>" +
        "<dt>Jail</dt><dd class=\"mono\">" + esc((d.jail || []).join("\n")) + "</dd>" +
        "<dt>Journal</dt><dd class=\"mono\">" + esc(d.audit_path) + "</dd>" +
        "<dt>Intégrité</dt><dd>" + (d.audit_intact ? "intègre ✓" : "ALTÉRÉE ✗") + "</dd>" +
        "<dt>Politique</dt><dd class=\"mono\">" + esc(d.policy_source) + "</dd>" +
        "<dt>Budget</dt><dd>" + d.max_steps + " étapes / " + d.max_seconds + " s</dd>" +
        "<dt>Uptime</dt><dd>" + esc((s.server || {}).uptime || 0) + " s</dd>" +
      "</dl></section>";
  }

  /* ------------------------------------------------------------ rendu --- */
  function render() {
    if (!state) return;
    var el = $("#view");
    var html = "";
    if (route === "conversation") html = viewConversation(state);
    else if (route === "outils") html = viewOutils(state);
    else if (route === "politique") html = viewPolitique(state);
    else if (route === "journal") html = viewJournal(state);
    else if (route === "sante") html = viewSante(state);
    else html = viewApercu(state);
    el.innerHTML = html;

    var form = document.getElementById("composer");
    if (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var input = document.getElementById("goal");
        var goal = (input.value || "").trim();
        if (!goal || busy) return;
        busy = true;
        document.getElementById("send").disabled = true;
        api("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ goal: goal })
        }).then(function () { input.value = ""; setTimeout(load, 250); })
          .catch(function () {}).then(function () {
            busy = false;
            var b = document.getElementById("send");
            if (b) b.disabled = false;
          });
      });
    }
  }

  /* ----------------------------------------------------------- modal ---- */
  function modal() {
    var wrap = $("#modal");
    var pending = state && state.pending && state.pending.length ? state.pending[0] : null;
    if (!pending) { wrap.hidden = true; return; }
    wrap.hidden = false;
    $("#m-risk").textContent = pending.risk || "—";
    $("#m-risk").className = "badge " +
      (pending.risk === "PRIVILEGED" || pending.risk === "DESTRUCTIVE" ? "deny" : "confirm");
    $("#m-summary").textContent = pending.summary || "—";
    $("#m-action").textContent = pending.action || "—";
    $("#m-target").textContent = pending.target || "—";
    $("#m-rule").textContent = pending.rule_id || "règle par défaut";
    $("#m-reason").textContent = pending.reason || "la politique exige une confirmation";
  }

  function decide(approve) {
    var pending = state && state.pending && state.pending[0];
    if (!pending) return;
    api("/api/decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: pending.id, approve: approve })
    }).then(function () { $("#modal").hidden = true; setTimeout(load, 200); })
      .catch(function () {});
  }
  $("#m-allow").addEventListener("click", function () { decide(true); });
  $("#m-deny").addEventListener("click", function () { decide(false); });

  /* --------------------------------------------------------- démarrage -- */
  setRoute();
  load();
  setInterval(load, 1400);
})();
