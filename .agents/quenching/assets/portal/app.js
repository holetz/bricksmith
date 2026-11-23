"use strict";
/* Quenching spec portal. No build, no CDN, no HTML strings: every node is created with
   textContent, so spec text can never inject markup. */
(() => {
  /* ── constants ───────────────────────────────────────────────────────────── */
  const SVGNS = "http://www.w3.org/2000/svg";
  const DAY = 86400000;
  const STAGE_PT = { captured: "capturada", ready: "pronta", approved: "aprovada",
                     executing: "em execução", blocked: "bloqueada", review: "em revisão",
                     archived: "arquivada" };
  const CONN_TEXT = { init: "conectando", live: "ao vivo", unstable: "instável", offline: "offline" };
  const THEMES = ["auto", "light", "dark"];
  const THEME_LABEL = { auto: "automático", light: "claro", dark: "escuro" };
  const TASK_FILTERS = [
    { key: "all", label: "Todas" },
    { key: "open", label: "Abertas" },
    { key: "done", label: "Feitas" },
    { key: "blocked", label: "Bloqueadas" },
  ];
  const COMPLEXITIES = ["xs", "low", "medium", "high", "xhigh"];
  const PRIORITY_BANDS = { critical: "P1 · crítica", high: "P2 · alta", medium: "P3 · média", low: "P4 · baixa" };
  function priorityShort(p) {
    const c = p && String(p.criticality || "").toLowerCase();
    if (PRIORITY_BANDS[c]) return "P" + (["critical", "high", "medium", "low"].indexOf(c) + 1);
    const n = Number(p && p.level);
    return Number.isFinite(n) && n >= 1 && n <= 4 ? "P" + n : "";
  }
  function priorityBand(p) {
    const c = p && String(p.criticality || "").toLowerCase();
    if (PRIORITY_BANDS[c]) return PRIORITY_BANDS[c];
    const n = Number(p && p.level);
    return Number.isFinite(n) && n >= 1 && n <= 4 ? `P${n} · nível` : "sem prioridade";
  }
  function complexityOf(s) {
    const c = String((s && s.complexity) || "").toLowerCase();
    return COMPLEXITIES.includes(c) ? c : "sem complexidade";
  }
  const GROUPINGS = [
    { key: "stage", label: "Etapa", of: (s) => s.stage },
    { key: "subject", label: "Assunto", of: (s) => s.subject || "sem assunto" },
    { key: "priority", label: "Prioridade", of: (s) => priorityBand(s.priority) },
    { key: "assignee", label: "Responsável", of: (s) => s.assignee || "sem responsável" },
    { key: "complexity", label: "Complexidade", of: (s) => complexityOf(s) },
    { key: "type", label: "Tipo", of: (s) => s.workItemType || "sem tipo" },
  ];
  const SORTS = [
    { key: "rank", label: "Ranking" },
    { key: "date-desc", label: "Mais nova" },
    { key: "date-asc", label: "Mais antiga" },
    { key: "title", label: "Título" },
    { key: "progress", label: "Progresso" },
  ];
  const GANTT_ZOOMS = { day: 26, week: 9, month: 3 };
  const SHORTCUTS = [
    { keys: "Ctrl/Cmd+K", label: "Abrir a paleta de comandos" },
    { keys: "/", label: "Buscar na paleta" },
    { keys: "?", label: "Abrir este mapa de atalhos" },
    { keys: "g b", label: "Ir para o board" },
    { keys: "g l", label: "Ir para o ranking" },
    { keys: "g d", label: "Ir para a fila de decisão" },
    { keys: "g e", label: "Ir para os épicos" },
    { keys: "g g", label: "Ir para o Gantt" },
    { keys: "g s", label: "Ir para as sessões" },
    { keys: "j / k", label: "Próximo / anterior na lista" },
    { keys: "Enter", label: "Abrir a gaveta" },
    { keys: "Ctrl/Cmd+Enter", label: "Abrir a página inteira" },
    { keys: "a", label: "Aprovar a spec em foco (com confirmação)" },
    { keys: "t", label: "Alternar o tema" },
    { keys: "Esc", label: "Fechar o diálogo" },
  ];

  const state = {
    specs: [], stages: [], fetchedAt: 0, ttl: 5, writable: false, why: "",
    epics: { supported: false, epics: [] }, human: null, busy: false,
    pending: { count: 0, specs: [], since: null, supported: false }, pushing: false,
    sessionsOn: false, sessions: null, chatOn: false, chatBudget: null,
    chat: { draft: "", worktree: "", job: null, status: "", events: [], next: 0, cost: null, error: "" },
    projects: null, project: "", base: "",
    board: { group: "stage", sort: "rank", q: "", filters: [], hide: false, compact: false,
             collapsed: [] },
    gantt: { zoom: "week", deps: true, crit: true, scrollLeft: 0, centerDay: null },
    taskFilter: "all", detail: null, pageDetail: null,
    conn: "init", lastOk: 0, lastError: 0, paused: false,
    events: [], lastDataLog: 0,
    searchFacets: null, searchFor: "",
    loading: true, loadError: "", lastLoadMs: 0,
  };

  /* ── helpers ─────────────────────────────────────────────────────────────── */
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "on") { for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn); }
      else if (k === "class") el.className = v;
      else if (k === "style") el.style.cssText = v;
      else if (k in el && k !== "list" && k !== "form") el[k] = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  function svg(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, String(v));
    for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  /* ── icons: one authored 16px set, 1.5 stroke, round caps. Paths only, drawn with
        createElementNS so the CSP stays untouched. ─────────────────────────────────────── */
  const ICONS = {
    board: ["M2.5 2.5h3v11h-3z", "M6.5 2.5h3v7h-3z", "M10.5 2.5h3v9h-3z"],
    list: ["M3 13.5v-4h3v4", "M6.5 13.5v-11h3v11", "M10 13.5v-7h3v7"],
    human: ["M8 13.5v-5L3.5 5.5", "M8 8.5l4.5-3", "M3.5 2.5v3h3", "M12.5 2.5v3h-3"],
    epics: ["M8 2l6 3-6 3-6-3 6-3Z", "M2 8l6 3 6-3", "M2 11l6 3 6-3"],
    gantt: ["M2.5 3.5h6", "M5.5 8h6", "M8.5 12.5h5"],
    sessions: ["M2.5 3h11v10h-11z", "M5 6l2.5 2L5 10", "M8.5 10.5H11"],
    chat: ["M2.5 3h11v8H7l-3 2.5V11H2.5z"],
    search: ["M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10Zm7 2-3.5-3.5"],
    theme: ["M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12Zm0-12v12", "M8 5h3.6", "M8 8h4.9", "M8 11h3.6"],
    help: ["M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Z", "M6.1 6.2a2 2 0 1 1 2.9 1.8c-.6.3-1 .8-1 1.5", "M8 11.6v.1"],
    plus: ["M8 3v10", "M3 8h10"],
    filter: ["M2.5 4h11", "M5 8h6", "M7 12h2"],
    down: ["m4 6 4 4 4-4"],
    right: ["m6 4 4 4-4 4"],
    up: ["m4 10 4-4 4 4"],
    x: ["m4 4 8 8", "M12 4l-8 8"],
    copy: ["M5.5 5.5h7v8h-7z", "M3.5 10.5v-8h7"],
    panel: ["M2.5 3h11v10h-11z", "M9.5 3v10"],
    pause: ["M5.5 3.5v9", "M10.5 3.5v9"],
    refresh: ["M13 8a5 5 0 1 1-1.6-3.7", "M13 2v3h-3"],
    bell: ["M4 11.5V7.2a4 4 0 0 1 8 0v4.3l1 1.2H3l1-1.2", "M6.6 14.5h2.8"],
    alert: ["M8 2.5 14.5 13.5h-13L8 2.5Z", "M8 6.5v3.2", "M8 11.8v.1"],
    check: ["m3 8.5 3.2 3L13 4.5"],
  };
  function icon(name) {
    return svg("svg", { class: "i", viewBox: "0 0 16 16", "aria-hidden": "true" },
      ICONS[name].map((d) => svg("path", { d })));
  }
  function nodes(...kids) { return kids.flat(Infinity).filter((k) => k != null && k !== false); }
  function safeHref(url) { return typeof url === "string" && /^https?:\/\//i.test(url) ? url : ""; }
  function prUrl(pr) {
    if (typeof pr === "string") return safeHref(pr);
    return pr && typeof pr === "object" ? safeHref(pr.url) : "";
  }
  function txt(v) { return v == null || v === "" ? "—" : String(v); }
  function pad(n) { return String(n).padStart(2, "0"); }
  function clockOf(ts) {
    const d = new Date(ts);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso + "T00:00:00Z");
    if (Number.isNaN(d.getTime())) return String(iso);
    return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()}`;
  }
  function ageDays(iso) {
    if (!iso) return null;
    const t = Date.parse(iso + "T00:00:00Z");
    if (Number.isNaN(t)) return null;
    return Math.max(0, Math.floor((Date.now() - t) / DAY));
  }
  function ageLabel(iso) {
    const d = ageDays(iso);
    if (d == null) return "—";
    if (d === 0) return "hoje";
    if (d === 1) return "ontem";
    if (d < 30) return `há ${d} d`;
    const m = Math.floor(d / 30);
    return `há ${m} ${m === 1 ? "mês" : "meses"}`;
  }
  function dayOf(iso) { return Date.parse(iso + "T00:00:00Z") / DAY; }
  function isoOf(day) { return new Date(day * DAY).toISOString().slice(0, 10); }
  function slug(v) { return encodeURIComponent(String(v)).replace(/[%]/g, "_"); }
  function byKey(key) { return $$("[data-fkey]").find((el) => el.getAttribute("data-fkey") === key) || null; }
  function setPressed(btn, on) { if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false"); }

  /* ── preferences: every key carries the q2. prefix, and private mode is tolerated ── */
  function prefsGet(key, fallback = null) {
    try { const v = localStorage.getItem("q2." + key); return v == null ? fallback : v; }
    catch (e) { return fallback; }
  }
  function prefsSet(key, value) {
    try { localStorage.setItem("q2." + key, String(value)); } catch (e) { /* private mode */ }
  }
  function prefsJSON(key, fallback) {
    const raw = prefsGet(key);
    if (!raw) return fallback;
    try { const v = JSON.parse(raw); return v == null ? fallback : v; } catch (e) { return fallback; }
  }
  function storageOk() { try { localStorage.setItem("q2.probe", "1"); localStorage.removeItem("q2.probe"); return true; } catch (e) { return false; } }

  /* ── theme and density ───────────────────────────────────────────────────── */
  function theme() { const t = prefsGet("theme", "auto"); return THEMES.includes(t) ? t : "auto"; }
  function applyTheme() {
    const t = theme();
    document.documentElement.setAttribute("data-theme", t);
    document.documentElement.setAttribute("data-density", prefsGet("density", "comfortable"));
    const btn = $("#theme");
    if (btn) {
      btn.setAttribute("aria-label", `Tema: ${THEME_LABEL[t]} (t alterna)`);
      btn.setAttribute("title", `Tema: ${THEME_LABEL[t]} (t)`);
      if (!btn.querySelector("svg.i")) btn.prepend(icon("theme"));
    }
  }
  function cycleTheme() {
    const i = THEMES.indexOf(theme());
    prefsSet("theme", THEMES[(i + 1) % THEMES.length]);
    applyTheme(); paintFavicon();
    logEvent("action", `Tema: ${THEME_LABEL[theme()]}`);
  }
  function toggleDensity() {
    const next = prefsGet("density", "comfortable") === "compact" ? "comfortable" : "compact";
    prefsSet("density", next); applyTheme();
    logEvent("action", `Densidade: ${next === "compact" ? "compacta" : "confortável"}`);
    render();
  }

  /* ── api client and connection ───────────────────────────────────────────── */
  const LIST_READ = /^(specs|epics|human|sessions|schedule)(\?|$)/;
  const validated = {};
  let changed = false;

  async function api(path, opts = {}) {
    const init = { method: opts.body ? "POST" : "GET", headers: {}, credentials: "same-origin" };
    const key = state.base + path;
    const kept = !opts.body && LIST_READ.test(path) ? validated[key] : null;
    if (kept) init.headers["If-None-Match"] = kept.etag;
    if (opts.body) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    let res, data;
    try {
      res = await fetch(state.base + "/api/" + path, init);
      if (res.status === 304 && kept) { markConn(true); return kept.data; }
      data = await res.json();
    } catch (e) {
      markConn(false);
      throw { code: "network", message: "o servidor do portal não respondeu" };
    }
    if (!res.ok || data.ok === false) {
      if (res.status >= 500) markConn(false);
      throw { code: data.code || "http-" + res.status, message: data.message || res.statusText, data };
    }
    markConn(true);
    const etag = res.headers.get("ETag");
    if (etag && !opts.body && LIST_READ.test(path)) {
      const prev = validated[key];
      if (!prev || prev.etag !== etag) changed = true;
      validated[key] = { etag, data };
    }
    return data;
  }
  function connState() {
    if (!state.lastOk) return state.lastError ? "offline" : "init";
    if (state.lastError > state.lastOk) return Date.now() - state.lastError < 30000 ? "unstable" : "offline";
    const age = Date.now() - state.lastOk;
    return age <= Math.max(10000, state.ttl * 2000) ? "live" : "unstable";
  }
  function markConn(ok) {
    if (ok) state.lastOk = Date.now(); else state.lastError = Date.now();
    const next = connState();
    if (next !== state.conn) {
      state.conn = next;
      logEvent("conn", `Conexão: ${CONN_TEXT[next]}`);
      paintConn();
    }
  }
  function paintConn() {
    const box = $("#conn");
    if (!box) return;
    const s = state.conn;
    box.className = "conn " + s;
    const label = box.lastElementChild || box;
    label.textContent = CONN_TEXT[s];
    const banner = $("#connbanner");
    if (s === "live" || s === "init") { banner.hidden = true; banner.replaceChildren(); return; }
    banner.hidden = false;
    banner.replaceChildren(
      s === "offline"
        ? `Sem resposta do servidor${state.lastOk ? " desde " + clockOf(state.lastOk) : ""}. O último snapshot continua na tela.`
        : `Conexão instável${state.lastOk ? " — último dado às " + clockOf(state.lastOk) : ""}.`,
      h("button", { type: "button", class: "ghost small", on: { click: () => refresh(true) } }, "Tentar de novo"));
  }

  /* ── toasts, notifications and the event centre ──────────────────────────── */
  function toast(msg, ok, action) {
    const t = h("div", { class: "toast" + (ok ? " ok" : "") }, msg,
      action ? h("button", { type: "button", class: "ghost small", on: { click: () => { action.run(); t.remove(); } } }, action.label) : null);
    $("#toasts").append(t);
    setTimeout(() => t.remove(), ok ? 3500 : 9000);
  }
  function fail(e) { toast([h("code", {}, e && e.code ? e.code : "erro"), ": " + (e && e.message ? e.message : "falha")]); }
  function logEvent(kind, text, href) {
    state.events.unshift({ at: Date.now(), kind, text, href, unread: true });
    if (state.events.length > 50) state.events.length = 50;
    paintEvents();
  }
  function unread() { return state.events.filter((e) => e.unread).length; }
  function paintEvents() {
    const n = unread();
    const badge = $("#events-count");
    if (badge) { badge.textContent = n > 99 ? "99+" : String(n); badge.hidden = n === 0; }
    paintEventList();
  }
  function paintEventList() {
    const box = $("#events");
    if (!box || box.hidden) return;
    box.replaceChildren(
      h("div", { class: "events-head" },
        h("h2", {}, "Notificações"),
        h("button", { id: "notify", type: "button", class: "ghost small", "aria-pressed": "false", on: { click: toggleNotify } }, "Notificações: desligadas"),
        h("button", { type: "button", class: "ghost small", on: { click: () => { state.events = []; paintEvents(); } } }, "Limpar")),
      state.events.length
        ? h("ol", { class: "events-list" }, state.events.map((e) => h("li", { class: "ev " + e.kind },
            h("time", { datetime: new Date(e.at).toISOString() }, clockOf(e.at)),
            e.href ? h("a", { href: e.href, on: { click: () => closeEvents() } }, e.text) : h("span", {}, e.text))))
        : h("p", { class: "empty" }, "Nada por aqui. O histórico vive só nesta aba e recarregar zera."));
    paintNotify();
  }
  function toggleEvents() {
    const box = $("#events");
    box.hidden = !box.hidden;
    $("#events-open").setAttribute("aria-expanded", box.hidden ? "false" : "true");
    if (!box.hidden) { state.events.forEach((e) => { e.unread = false; }); paintEvents(); paintEventList(); }
  }
  function closeEvents() {
    const box = $("#events");
    if (!box || box.hidden) return;
    box.hidden = true;
    $("#events-open").setAttribute("aria-expanded", "false");
  }
  function notifyOn() { return prefsGet("notify") === "1" && typeof Notification !== "undefined" && Notification.permission === "granted"; }
  function notifyGrowth(before, now) {
    if (before == null || now <= before) return;
    logEvent("data", `${now} ${now === 1 ? "spec espera" : "specs esperam"} por você`, "#/human");
    if (!notifyOn()) return;
    const first = state.human
      ? (state.human.awaitingApproval[0] || state.human.epicMembers[0] || state.human.blocked[0])
      : null;
    try {
      const n = new Notification("Quenching specs", { body: `${now} ${now === 1 ? "spec espera" : "specs esperam"} por você` });
      n.onclick = () => { if (first) openDrawer(first.id); else location.hash = "#/human"; n.close(); };
    } catch (e) { /* unsupported */ }
  }
  function paintNotify() {
    const b = $("#notify");
    if (!b) return;
    if (typeof Notification === "undefined") { b.hidden = true; return; }
    b.textContent = notifyOn() ? "Notificações: ligadas" : "Notificações: desligadas";
    b.setAttribute("aria-pressed", notifyOn() ? "true" : "false");
  }
  async function toggleNotify() {
    if (notifyOn()) { prefsSet("notify", "0"); paintNotify(); return; }
    const perm = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
    prefsSet("notify", perm === "granted" ? "1" : "0");
    if (perm !== "granted") toast("O navegador não autorizou as notificações.");
    paintNotify();
  }

  /* ── load and refresh ────────────────────────────────────────────────────── */
  async function load(refreshFlag) {
    changed = false;
    const before = state.human ? state.human.count : null;
    const started = Date.now();
    /* The three reads are independent: one slow endpoint must not hide the other two, and
       the board no longer waits for the queue and the epic program in sequence. */
    const [specs, epics, human] = await Promise.allSettled([
      api("specs" + (refreshFlag ? "?refresh=1" : "")), api("epics"), api("human")]);
    if (specs.status === "rejected") {
      state.loading = false;
      state.loadError = (specs.reason && specs.reason.message) || "falha ao ler as specs";
      throw specs.reason;
    }
    const data = specs.value;
    state.specs = data.specs; state.stages = data.stages; state.ttl = data.ttlSeconds;
    state.pending = data.pending || { count: 0, specs: [], since: null, supported: false };
    state.fetchedAt = Date.now();
    state.loading = false; state.loadError = "";
    state.lastLoadMs = Date.now() - started;
    state.epics = epics.status === "fulfilled" ? epics.value : { supported: false, epics: [] };
    state.human = human.status === "fulfilled" ? human.value : null;
    $("#nav-epics").hidden = !state.epics.supported;
    if (state.sessionsOn) await loadSessions();
    paintHumanCount();
    paintPending();
    notifyGrowth(before, state.human ? state.human.count : 0);
    if (changed && Date.now() - state.lastDataLog > 60000) {
      state.lastDataLog = Date.now();
      logEvent("data", `Snapshot atualizado · ${state.specs.length} specs`);
    }
    paintPulse();
    tickFreshness();
    return changed;
  }
  async function loadSessions() {
    try { state.sessions = await api("sessions"); } catch (e) { state.sessions = null; }
    paintSessionsCount();
  }
  async function loadConfig() {
    try {
      const c = await api("config");
      state.writable = c.writable; state.why = c.readOnlyReason; state.sessionsOn = !!c.sessions;
      state.chatOn = !!c.chat; state.chatBudget = c.chatBudgetUsd;
    } catch (e) { fail(e); }
    $("#nav-sessions").hidden = !state.sessionsOn;
    $("#nav-chat").hidden = !state.chatOn;
    $("#capture").disabled = !state.writable;
    paintPending();
    const b = $("#banner");
    b.hidden = state.writable;
    b.textContent = state.writable ? "" : "Somente leitura: " + state.why + ". As ações de escrita estão desligadas no servidor.";
  }
  function tickFreshness() {
    const el = $("#fresh");
    if (el) {
      if (!state.fetchedAt) el.textContent = "carregando";
      else {
        const s = Math.max(0, Math.round((Date.now() - state.fetchedAt) / 1000));
        const rel = s < 60 ? s + " s" : Math.floor(s / 60) + " min";
        el.textContent = `atualizado há ${rel} · ${clockOf(state.fetchedAt)}`;
        el.classList.toggle("stale", s > 120);
      }
    }
    const next = connState();
    if (next !== state.conn) { state.conn = next; paintConn(); }
  }
  async function refresh(force) {
    if (state.busy) return;
    state.busy = true;
    $("#refresh") && ($("#refresh").disabled = true);
    try {
      const did = await load(force);
      if (force || did) await render();
    } catch (e) { fail(e); paintConn(); }
    finally { state.busy = false; $("#refresh") && ($("#refresh").disabled = false); }
  }
  function togglePause() {
    state.paused = !state.paused;
    prefsSet("autorefresh", state.paused ? "0" : "1");
    logEvent("action", state.paused ? "Auto-refresh pausado" : "Auto-refresh retomado");
    paintPause();
  }
  function paintPause() {
    const b = $("#pause");
    if (!b) return;
    b.replaceChildren(icon("pause"), state.paused ? "Retomar" : "Pausar");
    b.setAttribute("aria-pressed", state.paused ? "true" : "false");
    b.classList.toggle("is-paused", state.paused);
  }

  /* ── shell painting ──────────────────────────────────────────────────────── */
  function paintHumanCount() {
    const n = state.human ? state.human.count : 0;
    const el = $("#human-count");
    if (el) { el.textContent = n; el.classList.toggle("hot", n > 0); }
    document.title = (n > 0 ? `(${n}) ` : "") + (state.project ? state.project + " — " : "") + "Quenching specs";
    paintFavicon();
  }
  function paintFavicon() {
    const c = document.createElement("canvas");
    c.width = c.height = 32;
    const g = c.getContext && c.getContext("2d");
    if (!g) return;
    const css = getComputedStyle(document.documentElement);
    const hot = state.human ? state.human.count : 0;
    g.fillStyle = hot > 0 ? (css.getPropertyValue("--accent").trim() || "#b4410a")
                          : (css.getPropertyValue("--muted").trim() || "#5f5c56");
    g.beginPath(); g.arc(16, 16, 15, 0, 2 * Math.PI); g.fill();
    g.fillStyle = css.getPropertyValue("--accent-ink").trim() || "#ffffff";
    g.font = "bold 20px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(hot > 99 ? "99" : String(hot), 16, 17);
    $("#favicon").href = c.toDataURL("image/png");
  }
  /* The pending-write badge: only a writable portal over a store that reports pending state
     shows it. Publishing is one explicit gesture, and the button says so while it runs. */
  function paintPending() {
    const b = $("#push");
    if (!b) return;
    const p = state.pending || {};
    const n = p.count || 0;
    if (!state.writable || p.supported === false || !n) { b.hidden = true; return; }
    b.hidden = false;
    b.disabled = !!state.pushing;
    b.setAttribute("aria-busy", state.pushing ? "true" : "false");
    $("#push-label").textContent = state.pushing ? "Enviando…" : "Enviar";
    $("#push-count").hidden = !!state.pushing;
    $("#push-count").textContent = String(n);
    b.setAttribute("aria-label", state.pushing ? "Publicando as escritas locais"
      : `${n} ${n === 1 ? "spec com escrita local ainda não publicada" : "specs com escritas locais ainda não publicadas"}`);
  }
  function paintSessionsCount() {
    const n = state.sessions ? state.sessions.sessions.filter(isWaiting).length : 0;
    const el = $("#sessions-count");
    el.textContent = n;
    el.classList.toggle("hot", n > 0);
  }
  function pulseCell(label, value, href, opts = {}) {
    return h("a", { class: "pulse-cell" + (opts.cls ? " " + opts.cls : ""), href,
        title: opts.hint || label, "aria-label": `${label}: ${value}` },
      h("strong", { class: "pulse-value" }, value),
      label,
      opts.spec ? h("span", { class: "pulse-spec" },
        h("span", { class: "id" }, opts.spec.id),
        h("span", { class: "pulse-title" }, opts.spec.title)) : null);
  }
  function paintPulse() {
    const box = $("#pulse");
    if (!box) return;
    if (!state.specs.length && !state.human) {
      box.replaceChildren(...Array.from({ length: 6 }, () => h("div", { class: "pulse-cell skeleton", "aria-hidden": "true" })));
      return;
    }
    const open = state.specs.filter((s) => s.phase === "plans");
    const running = open.filter((s) => s.stage === "executing").length;
    const blocked = open.filter((s) => s.stage === "blocked").length;
    const waiting = state.human ? state.human.count : null;
    const tasks = open.reduce((a, s) => ({ c: a.c + (s.progress ? s.progress.checked : 0), t: a.t + (s.progress ? s.progress.total : 0) }), { c: 0, t: 0 });
    const oldest = open.reduce((a, s) => (s.date && (!a || s.date < a.date) ? s : a), null);
    box.replaceChildren(
      pulseCell("esperando você", waiting == null ? "—" : String(waiting), "#/human", { cls: "hot", hint: "fila de decisão do humano" }),
      pulseCell("bloqueadas", String(blocked), "#/board?f=stage:blocked", { cls: blocked ? "bad" : "", hint: "specs com tarefa bloqueada" }),
      pulseCell("em execução", String(running), "#/board?f=stage:executing", { hint: "specs na etapa em execução" }),
      pulseCell("abertas", String(open.length), "#/board", { hint: "specs em plans" }),
      pulseCell("tarefas", tasks.t ? `${tasks.c}/${tasks.t}` : "—", "#/list", { hint: "tarefas concluídas nas specs abertas" }),
      pulseCell("mais antiga", oldest ? ageLabel(oldest.date) : "—", "#/list",
        { hint: oldest ? "aberta desde " + fmtDate(oldest.date) : "sem data",
          spec: oldest ? { id: oldest.id, title: oldest.title } : null }));
  }
  function emptyState(title, hint, action) {
    return h("div", { class: "empty-state" },
      h("p", { class: "empty-title" }, title),
      hint ? h("p", { class: "empty-hint" }, hint) : null,
      action || null);
  }
  /* First paint is the shell, not a blank page: the skeleton says what is happening and
     reserves the board's shape, so the page does not jump when the snapshot lands. */
  function skeletonCard() {
    return h("div", { class: "sk-card" }, h("span", { class: "sk sk-line w70" }),
      h("span", { class: "sk sk-line w95" }), h("span", { class: "sk sk-line w45" }));
  }
  function loadingView() {
    const columns = ["prontas", "em execução", "esperando você"].map((name) =>
      h("section", { class: "col" },
        h("h2", { class: "col-head" }, h("button", { type: "button", class: "col-toggle" },
          h("span", { class: "col-name" }, name), h("span", { class: "count" }, "·"))),
        h("div", { class: "col-body" }, skeletonCard(), skeletonCard())));
    return h("div", { class: "loading", role: "status", "aria-live": "polite", "aria-busy": "true" },
      h("p", { class: "loading-line" }, h("i", { class: "dot pulse", "aria-hidden": "true" }),
        "Lendo o repositório de specs…"),
      h("div", { class: "board skeleton-board", "aria-hidden": "true" }, columns));
  }
  function errorView() {
    return emptyState("O portal não conseguiu ler o repositório",
      state.loadError || "a resposta não chegou",
      h("button", { type: "button", class: "primary", on: { click: () => refresh(true) } }, "Tentar de novo"));
  }

  /* ── generic dialog ──────────────────────────────────────────────────────── */
  let dialogOpener = null;
  function openDialog(title, fields, submitLabel, onSubmit) {
    const dlg = $("#dlg");
    dialogOpener = document.activeElement;
    const form = h("form", { method: "dialog", on: { submit: async (e) => {
      e.preventDefault();
      const values = {};
      fields.forEach((f) => { values[f.name] = form.elements[f.name].value; });
      try { await onSubmit(values); dlg.close(); } catch (err) { fail(err); }
    } } },
      h("h2", { id: "dlg-title" }, title),
      fields.map((f) => h("label", {}, f.label, f.multiline
        ? h("textarea", { name: f.name, required: !!f.required, value: f.value || "" })
        : h("input", { type: "text", name: f.name, required: !!f.required, value: f.value || "" }))),
      h("div", { class: "actions" },
        h("button", { type: "button", on: { click: () => dlg.close() } }, "Cancelar"),
        h("button", { type: "submit", class: "primary" }, submitLabel)));
    dlg.replaceChildren(form);
    if (!dlg.open) dlg.showModal();
    const first = form.querySelector("input,textarea"); if (first) first.focus();
  }
  $("#dlg").addEventListener("close", () => { if (dialogOpener && dialogOpener.isConnected) dialogOpener.focus(); dialogOpener = null; });

  function captureDialog() {
    if (!state.writable) { toast("O portal está em somente leitura."); return; }
    openDialog("Capturar uma spec", [{ name: "title", label: "Título", required: true },
      { name: "problem", label: "Problema", multiline: true, required: true }], "Capturar",
      async (v) => { const r = await api("capture", { body: v }); toast(`Capturada #${r.id}`, true); logEvent("action", `Spec #${r.id} capturada`); await load(true); location.hash = "#/spec/" + r.id; await render(); });
  }
  function sectionDialog(id, sec) {
    if (!state.writable) { toast("O portal está em somente leitura."); return; }
    openDialog(`Editar ## ${sec.heading}`, [{ name: "body", label: "Markdown da seção", multiline: true, value: sec.body }], "Salvar",
      async (v) => { await api("section", { body: { spec: id, heading: sec.heading, body: v.body } }); toast("Seção salva", true); logEvent("action", `Seção ${sec.heading} de #${id} salva`); await load(true); await render(); });
  }
  async function pushNow() {
    if (state.pushing || !state.pending || !state.pending.count) return;
    if (!state.writable) { toast("O portal está em somente leitura."); return; }
    state.pushing = true; paintPending();
    try {
      const r = await api("push", { body: {} });
      const sent = (r.sent || []).length;
      toast(sent ? `Enviado: ${sent} ${sent === 1 ? "spec" : "specs"} ao remoto.` : "Nada pendente.", true);
      logEvent("action", sent ? `${sent} ${sent === 1 ? "spec publicada" : "specs publicadas"} ao remoto` : "Publicação sem escritas pendentes");
      for (const warn of r.cardFailures || []) {
        toast("Card não atualizado: " + (warn.id || warn.spec || JSON.stringify(warn)));
      }
      await load(true); await render();
    } catch (e) { fail(e); }
    finally { state.pushing = false; paintPending(); }
  }
  function discoveryDialog(id) {
    if (!state.writable) { toast("O portal está em somente leitura."); return; }
    openDialog("Registrar uma descoberta", [{ name: "text", label: "O que você encontrou?", required: true }], "Adicionar",
      async (v) => { await api("discovery", { body: { spec: id, text: v.text } }); toast("Descoberta registrada", true); logEvent("action", `Descoberta em #${id}`); await load(true); await render(); });
  }

  /* ── palette ─────────────────────────────────────────────────────────────── */
  const palette = { items: [], index: 0, opener: null, mode: "all" };
  function actions() {
    return [
      { group: "Ações", label: "Ir para o board", keys: "g b", run: () => go("board") },
      { group: "Ações", label: "Ir para o ranking", keys: "g l", run: () => go("list") },
      { group: "Ações", label: "Ir para a fila de decisão", keys: "g d", run: () => go("human") },
      { group: "Ações", label: "Ir para os épicos", keys: "g e", run: () => go("epics") },
      { group: "Ações", label: "Ir para o Gantt", keys: "g g", run: () => go("gantt") },
      { group: "Ações", label: state.sessionsOn ? "Ir para as sessões" : "Sessões (indisponível)", keys: "g s", run: () => go("sessions") },
      { group: "Ações", label: `Tema: ${THEME_LABEL[theme()]}`, keys: "t", run: cycleTheme },
      { group: "Ações", label: "Alternar densidade", run: toggleDensity },
      { group: "Ações", label: state.paused ? "Retomar o auto-refresh" : "Pausar o auto-refresh", run: togglePause },
      { group: "Ações", label: "Atualizar agora", run: () => refresh(true) },
      { group: "Ações", label: "Capturar uma spec", run: captureDialog },
      ...(state.writable && state.pending.count ? [{ group: "Ações",
        label: `Enviar ${state.pending.count} ${state.pending.count === 1 ? "escrita pendente" : "escritas pendentes"} ao remoto`,
        run: pushNow }] : []),
      { group: "Ações", label: "Abrir a ajuda e os atalhos", keys: "?", run: openHelp },
    ];
  }
  function isSubsequence(needle, hay) {
    let i = 0;
    for (const ch of hay) { if (ch === needle[i]) i++; if (i === needle.length) return true; }
    return needle.length === 0;
  }
  function scoreSpec(s, q) {
    const n = q.toLowerCase();
    const id = String(s.id).toLowerCase(), title = (s.title || "").toLowerCase();
    if (id === n) return 100;
    if (id.startsWith(n)) return 90;
    if (title.startsWith(n)) return 70;
    if (title.includes(n)) return 55;
    if ((s.tags || []).some((t) => String(t).toLowerCase().includes(n))) return 45;
    if ((s.subject || "").toLowerCase().includes(n)) return 40;
    if (isSubsequence(n, id + " " + title)) return 20;
    return 0;
  }
  function paletteItems(q) {
    const needle = q.trim();
    const acts = actions().filter((a) => !needle || isSubsequence(needle.toLowerCase(), a.label.toLowerCase()) || a.label.toLowerCase().includes(needle.toLowerCase()));
    const out = acts.slice(0, 6).map((a) => ({ ...a, kind: "action" }));
    if (needle) {
      const specs = state.specs.map((s) => ({ s, score: scoreSpec(s, needle) })).filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score || a.s.rank - b.s.rank).slice(0, 8)
        .map((x) => ({ group: "Specs", label: `${x.s.id} · ${x.s.title || "(sem título)"}`, keys: x.s.stage, spec: x.s, run: () => openDrawer(x.s.id, palette.opener) }));
      out.push(...specs);
      if (specs.length) out.push({ group: "Specs", label: `Ver todos os resultados para “${needle}”`, keys: "Enter", run: () => go("search/" + encodeURIComponent(needle)) });
    } else {
      const recent = prefsJSON("recent", []).map((id) => state.specs.find((s) => String(s.id) === String(id))).filter(Boolean).slice(0, 5);
      out.push(...recent.map((s) => ({ group: "Recentes", label: `${s.id} · ${s.title || "(sem título)"}`, keys: s.stage, spec: s, run: () => openDrawer(s.id, palette.opener) })));
    }
    return out;
  }
  function renderPalette(q) {
    const list = $("#palette-list");
    const items = paletteItems(q);
    palette.items = items;
    palette.index = Math.min(palette.index, Math.max(0, items.length - 1));
    list.replaceChildren(...items.map((it, i) => {
      const id = "palette-opt-" + i;
      return h("li", {
        id, role: "option", "aria-selected": i === palette.index ? "true" : "false",
        class: "palette-item" + (i === palette.index ? " is-active" : ""),
        on: { mouseenter: () => { palette.index = i; paintPaletteSelection(); },
              click: () => runPalette(i) },
      },
        h("span", { class: "palette-group" }, it.group),
        h("span", { class: "palette-label" }, it.label),
        it.keys ? h("kbd", {}, it.keys) : null);
    }));
    paintPaletteSelection();
    const status = $("#palette-status");
    status.textContent = items.length ? `${items.length} resultado(s)` : "Nenhum resultado";
    if (!items.length) $("#palette-input").removeAttribute("aria-activedescendant");
  }
  function paintPaletteSelection() {
    const opts = $$("#palette-list [role=option]");
    opts.forEach((el, i) => {
      const on = i === palette.index;
      el.classList.toggle("is-active", on);
      el.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (opts[palette.index]) $("#palette-input").setAttribute("aria-activedescendant", opts[palette.index].id);
    if (opts[palette.index] && opts[palette.index].scrollIntoView) opts[palette.index].scrollIntoView({ block: "nearest" });
  }
  function runPalette(i) {
    const it = palette.items[i];
    if (!it) return;
    if (it.spec) remember(it.spec.id);
    closePalette();
    try { it.run(); } catch (e) { fail(e); }
  }
  function remember(id) {
    const list = prefsJSON("recent", []).filter((x) => String(x) !== String(id));
    list.unshift(String(id));
    prefsSet("recent", JSON.stringify(list.slice(0, 12)));
  }
  function openPalette(mode) {
    const d = $("#palette");
    if (d.open) return;
    palette.mode = mode || "all";
    palette.index = 0;
    palette.opener = document.activeElement;
    $("#palette-input").value = "";
    $("#palette-input").placeholder = palette.mode === "search" ? "Buscar specs…" : "Buscar specs ou executar uma ação…";
    renderPalette("");
    d.showModal();
    $("#palette-input").focus();
  }
  function closePalette() { const d = $("#palette"); if (d.open) d.close(); }
  $("#palette").addEventListener("close", () => { if (palette.opener && palette.opener.isConnected) palette.opener.focus(); palette.opener = null; });
  $("#palette").addEventListener("click", (e) => { if (e.target === $("#palette")) closePalette(); });
  $("#palette-input").addEventListener("input", (e) => { palette.index = 0; renderPalette(e.target.value); });
  $("#palette-input").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); palette.index = Math.min(palette.items.length - 1, palette.index + 1); paintPaletteSelection(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); palette.index = Math.max(0, palette.index - 1); paintPaletteSelection(); }
    else if (e.key === "Home") { e.preventDefault(); palette.index = 0; paintPaletteSelection(); }
    else if (e.key === "End") { e.preventDefault(); palette.index = Math.max(0, palette.items.length - 1); paintPaletteSelection(); }
    else if (e.key === "Enter") { e.preventDefault(); runPalette(palette.index); }
  });

  /* ── help and shortcut map ───────────────────────────────────────────────── */
  function openHelp() {
    const d = $("#help");
    const opener = document.activeElement;
    d.replaceChildren(
      h("div", { class: "dialog-head" },
        h("h2", { id: "help-title" }, "Ajuda e atalhos"),
        h("button", { type: "button", class: "ghost", on: { click: () => d.close() } }, "Fechar")),
      h("div", { class: "help-grid" },
        h("section", {},
          h("h3", {}, "O que é este portal"),
          h("p", {}, "É a leitura local do store de specs: o board mostra o trabalho por etapa, o ranking mostra a prioridade global, “Decidir” mostra o que espera por você, “Épicos” mostra o programa, o Gantt mostra o cronograma derivado e “Sessões”/“Chat” aparecem quando o servidor os liga."),
          h("p", {}, "Nada aqui inventa dado: cada número sai do servidor. A etapa de uma spec é derivada das tarefas e da aprovação, não é um campo que se arrasta."),
          h("h3", {}, "O que cada estágio significa"),
          h("dl", { class: "glossary" },
            ["captured", "ready", "approved", "executing", "blocked", "review", "archived"].map((st) =>
              h("div", {}, h("dt", {}, h("span", { class: "chip stage-" + st }, st)), h("dd", {}, stageMeaning(st)))))),
        h("section", {},
          h("h3", {}, "Atalhos"),
          h("table", { class: "shortcuts" },
            h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Tecla"), h("th", { scope: "col" }, "Ação"))),
            h("tbody", {}, SHORTCUTS.map((s) => h("tr", {}, h("td", {}, h("kbd", {}, s.keys)), h("td", {}, s.label))))),
          h("h3", {}, "O que o portal não faz"),
          h("ul", { class: "plain" },
            h("li", {}, "Não move cartão entre etapas: a etapa é derivada."),
            h("li", {}, "Não edita tarefa nem marca checkbox: a tarefa é lida."),
            h("li", {}, "Não escreve fora das seis ações: capturar, editar seção, aprovar, reordenar, registrar descoberta e enviar."),
            h("li", {}, "Não busca em outro repositório: itens de fora aparecem, mas não abrem."))),
      ),
      h("div", { class: "actions" }, h("button", { type: "button", class: "primary", on: { click: () => d.close() } }, "Entendi")));
    if (!d.open) d.showModal();
    d.addEventListener("close", () => { if (opener && opener.isConnected) opener.focus(); }, { once: true });
  }
  function stageMeaning(st) {
    return {
      captured: "foi escrita, mas ainda não tem as seções de definição.",
      ready: "tem as seções e as tarefas; espera aprovação humana.",
      approved: "foi aprovada por uma pessoa e pode ser executada.",
      executing: "tem tarefa em andamento ou já concluída.",
      blocked: "tem tarefa marcada como bloqueada; o motivo está na spec.",
      review: "foi executada e espera revisão ou merge.",
      archived: "saiu do trabalho ativo; o resultado está no registro.",
    }[st] || st;
  }
  $("#help").addEventListener("click", (e) => { if (e.target === $("#help")) $("#help").close(); });

  /* ── board ───────────────────────────────────────────────────────────────── */
  function go(view) {
    const hash = "#/" + view;
    if (location.hash === hash) render(); else location.hash = hash;
  }
  function isAwaiting(id) {
    return !!(state.human && state.human.awaitingApproval.some((r) => String(r.id) === String(id)));
  }
  function cardMeta(s) {
    const links = [];
    if (safeHref(s.card)) links.push(h("a", { href: s.card, target: "_blank", rel: "noopener noreferrer" }, "card"));
    if (prUrl(s.pr)) links.push(h("a", { href: prUrl(s.pr), target: "_blank", rel: "noopener noreferrer" }, "PR"));
    return h("div", { class: "meta" },
      priorityShort(s.priority) ? h("span", { class: "chip", title: "prioridade: " + priorityBand(s.priority) }, priorityShort(s.priority)) : null,
      complexityOf(s) !== "sem complexidade" ? h("span", { class: "chip" }, complexityOf(s)) : null,
      s.run && s.run.state ? h("span", {
        class: "chip run" + (s.run.state === "failed" || s.run.state === "blocked" || s.run.verifier === "fail" ? " run-bad" : ""),
        title: "último passo do runner" + (s.run.date ? " em " + s.run.date : "") }, "run: " + s.run.state + (s.run.verifier ? " / " + s.run.verifier : "")) : null,
      s.subject ? h("span", { class: "chip" }, s.subject) : null,
      s.tags.map((t) => h("span", { class: "chip" }, "#" + t)),
      s.assignee ? h("span", {}, "@" + s.assignee) : null,
      s.date ? h("span", { class: "age", title: fmtDate(s.date) }, ageLabel(s.date)) : null,
      progress(s.progress), links);
  }
  function progress(p) {
    if (!p || !p.total) return null;
    return [h("span", { class: "progress-num" }, `${p.checked}/${p.total}`),
            h("span", { class: "bar", role: "img", "aria-label": `${p.checked} de ${p.total} tarefas concluídas` },
              h("i", { style: `width:${Math.round(100 * p.checked / p.total)}%` }))];
  }
  function stagePill(stage) {
    return h("span", { class: "chip stage-" + stage, title: STAGE_PT[stage] || stage }, stage);
  }
  function blockedWhy(id) {
    const row = state.human && state.human.blocked.find((b) => String(b.id) === String(id));
    return row ? row.reasons.map((r) => `${r.task}: ${r.reason || "(sem motivo)"}`).join("; ") : "";
  }
  function freshOf(iso) {
    const days = ageDays(iso);
    if (days == null) return "";
    if (days <= 1) return " fresh-1";
    if (days <= 3) return " fresh-2";
    if (days <= 7) return " fresh-3";
    return "";
  }
  function cardTop(s) {
    const pri = priorityShort(s.priority);
    return h("div", { class: "card-top" },
      h("span", { class: "id" }, String(s.id)),
      pri ? h("span", { class: "pri " + pri.toLowerCase(), title: priorityBand(s.priority) }, pri) : null,
      s.date ? h("span", { class: "age" + freshOf(s.date), title: fmtDate(s.date) }, ageLabel(s.date)) : null);
  }
  /* One signal per card: the block reason, else the progress, else the run's verdict, else nothing. */
  function cardSignal(s) {
    const why = s.progress && s.progress.blocked > 0 ? blockedWhy(s.id) || "(sem motivo escrito)" : "";
    if (why) return h("p", { class: "card-why", title: why }, icon("alert"), h("span", {}, why));
    if (s.progress && s.progress.total > 0) return h("div", { class: "card-signal" }, progress(s.progress));
    if (s.run && s.run.state) {
      const bad = s.run.state === "failed" || s.run.state === "blocked" || s.run.verifier === "fail";
      return h("div", { class: "card-signal" },
        h("span", { class: "run " + (bad ? "bad" : "pass"), title: "último passo do runner" + (s.run.date ? " em " + s.run.date : "") },
          icon(bad ? "alert" : "check"), `${s.run.state}${s.run.verifier ? ", " + s.run.verifier : ""}`));
    }
    return null;
  }
  function cardFoot(s) {
    if (!s.assignee && !s.subject) return null;
    return h("div", { class: "card-foot" },
      s.assignee ? h("span", { class: "who" }, h("span", { class: "av", "aria-hidden": "true" }, String(s.assignee).slice(0, 2).toLowerCase()), s.assignee) : null,
      s.subject ? h("span", { class: "subject" }, s.subject) : null);
  }
  function card(s) {
    const blocked = s.progress && s.progress.blocked > 0;
    return h("article", { class: "card" + (blocked ? " is-blocked" : ""), "data-spec": s.id, "data-fkey": "card:" + s.id, tabindex: "-1" },
      cardTop(s),
      h("a", { class: "title", href: "#/spec/" + encodeURIComponent(s.id), "data-open": s.id, on: { click: (e) => { e.preventDefault(); openDrawer(s.id, e.currentTarget); } } },
        s.title || "(sem título)"),
      cardSignal(s),
      cardFoot(s),
      h("div", { class: "quick" },
        h("button", { type: "button", class: "ghost icon small", "aria-label": "Abrir a gaveta", title: "abrir", on: { click: (e) => openDrawer(s.id, e.currentTarget) } }, icon("panel")),
        h("button", { type: "button", class: "ghost icon small", "aria-label": "Copiar o comando", title: "copiar comando", on: { click: () => copyCommand(s.id) } }, icon("copy")),
        isAwaiting(s.id) ? h("button", { type: "button", class: "primary small", on: { click: () => approve(s.id) } }, "aprovar") : null));
  }
  function filtered() {
    const b = state.board;
    return state.specs.filter((s) => {
      if (b.q && !matchSpec(s, b.q)) return false;
      for (const f of b.filters) {
        const value = f.key === "type" ? s.workItemType : s[f.key];
        if (String(value || "") !== f.value) return false;
      }
      return true;
    });
  }
  function matchSpec(s, q) {
    const n = q.toLowerCase();
    const hay = `${s.id} ${s.title || ""} ${s.summary || ""} ${(s.tags || []).join(" ")} ${s.subject || ""}`.toLowerCase();
    return hay.includes(n) || isSubsequence(n, hay);
  }
  function distinct(key) {
    const set = new Set();
    state.specs.forEach((s) => {
      if (key === "tag") (s.tags || []).forEach((v) => v && set.add(v));
      else if (key === "type") { if (s.workItemType) set.add(s.workItemType); }
      else if (key === "complexity") { if (complexityOf(s) !== "sem complexidade") set.add(complexityOf(s)); }
      else if (s[key]) set.add(s[key]);
    });
    return [...set].sort((a, b) => String(a).localeCompare(String(b), "pt-BR"));
  }
  function seedBoard(query) {
    const keys = ["group", "sort", "q", "f", "hide", "compact"];
    if (!keys.some((k) => query.has(k))) return;
    const b = { group: "stage", sort: "rank", q: "", filters: [], hide: false, compact: false, collapsed: state.board.collapsed };
    b.group = query.get("group") || "stage";
    b.sort = query.get("sort") || "rank";
    b.q = query.get("q") || "";
    b.hide = query.get("hide") === "1";
    b.compact = query.get("compact") === "1";
    query.getAll("f").forEach((raw) => {
      const i = raw.indexOf(":");
      if (i > 0) b.filters.push({ key: raw.slice(0, i), value: raw.slice(i + 1) });
    });
    state.board = b;
    state.board.collapsed = prefsJSON("collapsed." + (state.project || "local"), []);
  }
  function boardHash() {
    const b = state.board, p = new URLSearchParams();
    if (b.group !== "stage") p.set("group", b.group);
    if (b.sort !== "rank") p.set("sort", b.sort);
    if (b.q) p.set("q", b.q);
    b.filters.forEach((f) => p.append("f", f.key + ":" + f.value));
    if (b.hide) p.set("hide", "1");
    if (b.compact) p.set("compact", "1");
    const qs = p.toString();
    return "#/board" + (qs ? "?" + qs : "");
  }
  function syncBoardURL() {
    const hash = boardHash();
    if (location.hash !== hash) history.replaceState(null, "", hash);
  }
  function setBoard(patch) {
    Object.assign(state.board, patch);
    syncBoardURL();
    render();
  }
  function setFilter(key, value) {
    const rest = state.board.filters.filter((f) => f.key !== key);
    if (value) rest.push({ key, value });
    state.board.filters = rest;
    syncBoardURL();
    render();
  }
  function removeFilter(key, value) {
    state.board.filters = state.board.filters.filter((f) => !(f.key === key && f.value === value));
    syncBoardURL();
    render();
  }
  function filterValue(key) {
    const f = state.board.filters.find((x) => x.key === key);
    return f ? f.value : "";
  }
  function hasQuick(kind) {
    if (kind === "blocked") return filterValue("stage") === "blocked";
    if (kind === "executing") return filterValue("stage") === "executing";
    if (kind === "unassigned") return state.board.filters.some((f) => f.key === "assignee" && f.value === "");
    return false;
  }
  function quickFilter(kind) {
    if (kind === "blocked" || kind === "executing") setFilter("stage", hasQuick(kind) ? "" : kind);
    else if (kind === "unassigned") setFilter("assignee", hasQuick(kind) ? "" : "");
    else if (kind === "human") {
      const ids = new Set((state.human ? state.human.awaitingApproval : []).map((r) => String(r.id)));
      const specs = state.specs.filter((s) => ids.has(String(s.id)));
      toast(`${specs.length} spec(s) aguardando aprovação; abra a fila para decidir.`);
      go("human");
    }
  }
  function grouping(key) { return GROUPINGS.find((g) => g.key === key) || GROUPINGS[0]; }
  function sortSpecs(list) {
    const by = state.board.sort;
    const out = list.slice();
    if (by === "date-asc") out.sort((a, b) => String(a.date || "9999").localeCompare(String(b.date || "9999")) || a.rank - b.rank);
    else if (by === "date-desc") out.sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")) || a.rank - b.rank);
    else if (by === "title") out.sort((a, b) => String(a.title || "").localeCompare(String(b.title || ""), "pt-BR"));
    else if (by === "progress") out.sort((a, b) => {
      const ra = a.progress && a.progress.total ? a.progress.checked / a.progress.total : -1;
      const rb = b.progress && b.progress.total ? b.progress.checked / b.progress.total : -1;
      return rb - ra || a.rank - b.rank;
    });
    else out.sort((a, b) => a.rank - b.rank);
    return out;
  }
  function grouped(list) {
    const g = grouping(state.board.group);
    const map = new Map();
    list.forEach((s) => { const k = g.of(s); if (!map.has(k)) map.set(k, []); map.get(k).push(s); });
    let keys = [...map.keys()];
    if (g.key === "stage") keys = state.stages.filter((k) => map.has(k)).concat(keys.filter((k) => !state.stages.includes(k)));
    else keys.sort((a, b) => {
      const an = /^sem /.test(a), bn = /^sem /.test(b);
      if (an !== bn) return an ? 1 : -1;
      return String(a).localeCompare(String(b), "pt-BR");
    });
    return keys.map((k) => ({ key: k, specs: sortSpecs(map.get(k)) }));
  }
  function isCollapsed(key) { return state.board.collapsed.includes(key); }
  function toggleCollapse(key) {
    const list = state.board.collapsed.includes(key) ? state.board.collapsed.filter((x) => x !== key) : state.board.collapsed.concat([key]);
    state.board.collapsed = list;
    prefsSet("collapsed." + (state.project || "local"), JSON.stringify(list));
    syncBoardURL();
    render();
  }
  function viewsKey() { return "views." + (state.project || "local"); }
  function savedViews() { return prefsJSON(viewsKey(), []); }
  function saveView() {
    if (!storageOk()) { toast("Este navegador não permite guardar visões."); return; }
    const name = (prompt("Nome da visão:") || "").trim();
    if (!name) return;
    const list = savedViews().filter((v) => v.name !== name);
    list.unshift({ name, state: { group: state.board.group, sort: state.board.sort, q: state.board.q, filters: state.board.filters, hide: state.board.hide, compact: state.board.compact } });
    prefsSet(viewsKey(), JSON.stringify(list.slice(0, 12)));
    logEvent("action", `Visão “${name}” salva`);
    render();
  }
  function applyView(name) {
    const v = savedViews().find((x) => x.name === name);
    if (!v) return;
    Object.assign(state.board, JSON.parse(JSON.stringify(v.state)));
    syncBoardURL();
    render();
  }
  function removeView(name) {
    prefsSet(viewsKey(), JSON.stringify(savedViews().filter((v) => v.name !== name)));
    render();
  }
  function toolbar() {
    const b = state.board;
    const chips = [];
    if (b.q) chips.push(h("button", { type: "button", class: "fchip", on: { click: () => setBoard({ q: "" }) } }, `“${b.q}”`, h("span", { "aria-hidden": "true" }, " ×")));
    b.filters.forEach((f) => chips.push(h("button", { type: "button", class: "fchip", on: { click: () => removeFilter(f.key, f.value) } },
      `${labelOfKey(f.key)}: ${f.value || "vazio"}`, h("span", { "aria-hidden": "true" }, " ×"))));
    const select = (key, label, values, current, onchange) => h("label", { class: "control" }, label,
      h("select", { value: current, on: { change: (e) => onchange(e.target.value) } },
        values.map((v) => h("option", { value: v.key, selected: v.key === current }, v.label))));
    const facet = (key, label) => h("label", { class: "control" }, label,
      h("select", { value: filterValue(key), on: { change: (e) => setFilter(key, e.target.value) } },
        h("option", { value: "" }, "todos"),
        distinct(key).map((v) => h("option", { value: v, selected: filterValue(key) === v }, v))));
    const toggle = (kind, label) => h("button", { type: "button", class: "ghost small" + (hasQuick(kind) ? " on" : ""),
      "aria-pressed": hasQuick(kind) ? "true" : "false", on: { click: () => quickFilter(kind) } }, label);
    return h("section", { class: "board-tools", "aria-label": "Controles do board" },
      select("group", "Agrupar", GROUPINGS.map((g) => ({ key: g.key, label: g.label })), b.group, (v) => setBoard({ group: v })),
      select("sort", "Ordenar", SORTS, b.sort, (v) => setBoard({ sort: v })),
      h("details", { class: "pop" },
        h("summary", { class: "ghost small", "aria-pressed": chips.length ? "true" : "false" }, "Filtros"),
        h("div", { class: "pop-body" },
          facet("stage", "Etapa"), facet("subject", "Assunto"), facet("tag", "Tag"),
          facet("type", "Tipo"), facet("assignee", "Responsável"), facet("complexity", "Complexidade"))),
      h("span", { class: "tools-sep" }),
      h("div", { class: "chips quick" },
        toggle("blocked", "bloqueadas"), toggle("executing", "em execução"), toggle("unassigned", "sem responsável"),
        h("button", { type: "button", class: "ghost small", on: { click: () => quickFilter("human") } }, "aguardando você")),
      chips.length
        ? h("div", { class: "chips" }, chips,
            h("button", { type: "button", class: "ghost small", on: { click: () => setBoard({ q: "", filters: [] }) } }, "limpar tudo"))
        : null,
      h("span", { class: "grow" }),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: b.hide, on: { change: (e) => setBoard({ hide: e.target.checked }) } }), "ocultar vazias"),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: b.compact, on: { change: (e) => setBoard({ compact: e.target.checked }) } }), "compacto"),
      h("details", { class: "pop views" },
        h("summary", { class: "ghost small" }, "Visões"),
        h("div", { class: "pop-body" },
          h("button", { type: "button", class: "ghost small", on: { click: saveView } }, "Salvar visão atual"),
          savedViews().length
            ? h("ul", { class: "views-list" }, savedViews().map((v) => h("li", {},
                h("button", { type: "button", class: "link", on: { click: () => applyView(v.name) } }, v.name),
                h("button", { type: "button", class: "ghost small", "aria-label": `Remover a visão ${v.name}`, on: { click: () => removeView(v.name) } }, "×"))))
            : h("p", { class: "empty" }, storageOk() ? "Nenhuma visão salva." : "Este navegador não permite guardar visões."))));
  }
  function labelOfKey(key) {
    return { stage: "etapa", subject: "assunto", tag: "tag", type: "tipo", assignee: "responsável", complexity: "complexidade" }[key] || key;
  }
  function viewBoard() {
    syncBoardURL();
    const specs = filtered();
    const groups = grouped(specs);
    const visible = state.board.hide ? groups.filter((g) => g.specs.length) : groups;
    return [
      toolbar(),
      visible.length
        ? h("div", { class: "board" + (state.board.compact ? " compact" : "") }, visible.map((g, gi) => column(g, gi)))
        : emptyState("Nada neste recorte", "Nenhuma spec corresponde aos filtros atuais.",
            h("button", { type: "button", class: "ghost", on: { click: () => setBoard({ q: "", filters: [] }) } }, "Limpar os filtros")),
    ];
  }
  function column(g, gi) {
    const key = state.board.group + ":" + g.key;
    const collapsed = isCollapsed(key);
    const id = "col-" + gi;
    const stage = state.board.group === "stage" ? String(g.key) : null;
    return h("section", { class: "col" + (collapsed ? " is-collapsed" : ""), "data-stage": stage, "aria-label": String(g.key) },
      h("h2", { class: "col-head" },
        h("button", { type: "button", class: "col-toggle", "aria-expanded": collapsed ? "false" : "true",
          "aria-controls": collapsed ? null : id, on: { click: () => toggleCollapse(key) } },
          h("span", { class: "col-name" }, String(g.key)),
          h("span", { class: "count" }, g.specs.length))),
      collapsed ? null : h("div", { id, class: "col-body", "data-lane": key },
        g.specs.length ? g.specs.map(card) : h("p", { class: "col-empty" }, "nada aqui")));
  }
  function copyCommand(id) {
    const cmd = "/quenching-specs execute " + id;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(cmd).then(() => toast("Copiado: " + cmd, true), () => toast(cmd, true));
    } else toast(cmd, true);
  }

  /* ── safe markdown: headings, lists, tasks, code, quotes, tables, rules, links ── */
  function inline(text) {
    const out = [];
    const re = /(`[^`]+`)|(\[[^\]]+\]\([^)\s]+\))|(\*\*[^*]+\*\*)|(~~[^~]+~~)|(\*[^*\s][^*]*\*)|(_[^_\s][^_]*_)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      const t = m[0];
      if (m[1]) out.push(h("code", {}, t.slice(1, -1)));
      else if (m[2]) {
        const [, label, url] = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(t);
        const href = safeHref(url);
        out.push(href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, label) : label);
      } else if (m[3]) out.push(h("strong", {}, t.slice(2, -2)));
      else if (m[4]) out.push(h("del", {}, t.slice(2, -2)));
      else out.push(h("em", {}, t.slice(1, -1)));
      last = m.index + t.length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  function cells(line) {
    return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
  }
  function markdown(src) {
    const root = h("div", { class: "md" });
    const lines = String(src || "").replace(/\r/g, "").split("\n");
    let list = null, listTag = "", para = [], i = 0;
    const flushPara = () => { if (para.length) { root.append(h("p", {}, inline(para.join(" ")))); para = []; } };
    const flushList = () => { list = null; listTag = ""; };
    while (i < lines.length) {
      const line = lines[i];
      if (/^```/.test(line)) {
        flushPara(); flushList();
        const code = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
        root.append(h("pre", {}, h("code", {}, code.join("\n"))));
        i++; continue;
      }
      let m;
      if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
        flushPara(); flushList();
        root.append(h("h" + Math.min(5, m[1].length + 1), {}, inline(m[2])));
      } else if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        flushPara(); flushList();
        root.append(h("hr"));
      } else if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
        flushPara(); flushList();
        const head = cells(line); i += 2;
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(cells(lines[i])); i++; }
        root.append(h("table", {},
          h("thead", {}, h("tr", {}, head.map((c) => h("th", { scope: "col" }, inline(c))))),
          h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, inline(c))))))));
        continue;
      } else if (/^>\s?/.test(line)) {
        flushPara(); flushList();
        const buf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ""));
        root.append(h("blockquote", {}, h("p", {}, inline(buf.join(" ")))));
        continue;
      } else if ((m = /^\s*[-*+]\s+(?:\[([ xX!])\]\s+)?(.*)$/.exec(line)) || (m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
        flushPara();
        const isOrdered = /^\s*\d/.test(line);
        const tag = isOrdered ? "ol" : "ul";
        if (!list || listTag !== tag) { list = h(tag); listTag = tag; root.append(list); }
        const box = !isOrdered && m[1] !== undefined ? m[1] : undefined;
        const body = isOrdered ? m[1] : m[2];
        const check = box === undefined ? null
          : h("input", { type: "checkbox", disabled: true, checked: /[xX]/.test(box), "aria-label": box === "!" ? "bloqueada" : box === "x" || box === "X" ? "feita" : "aberta" });
        list.append(h("li", { class: check ? "task" : "" }, check, check ? " " : "", inline(body)));
      } else if (!line.trim()) { flushPara(); flushList(); }
      else { flushList(); para.push(line.trim()); }
      i++;
    }
    flushPara();
    return root;
  }

  /* ── detail: one builder for the drawer and the whole page ───────────────── */
  const isRemoteId = (id) => /[\/#]/.test(String(id));

  function specById(id) { return state.specs.find((s) => String(s.id) === String(id)) || null; }
  function plansByRank() { return state.specs.filter((s) => s.phase === "plans").sort((a, b) => a.rank - b.rank); }
  function neighbours(id) {
    const list = plansByRank(), i = list.findIndex((s) => String(s.id) === String(id));
    return { prev: i > 0 ? list[i - 1] : null, next: i >= 0 && i < list.length - 1 ? list[i + 1] : null };
  }
  function taskItems(d) {
    const f = state.taskFilter;
    const rows = d.tasks.filter((t) => f === "all" || (f === "open" && !t.checked && !t.blocked)
      || (f === "done" && t.checked) || (f === "blocked" && t.blocked));
    if (!rows.length) return [h("p", { class: "empty" }, "nenhuma tarefa neste filtro")];
    return rows.map((t) => h("li", { class: "task" + (t.blocked ? " is-blocked" : "") + (t.checked ? " is-done" : "") },
      h("input", { type: "checkbox", disabled: true, checked: !!t.checked, "aria-label": t.checked ? "feita" : t.blocked ? "bloqueada" : "aberta" }),
      h("div", { class: "task-body" },
        h("span", { class: "task-id" }, t.id), " ", h("span", { class: "task-text" }, t.text),
        t.blocked ? h("div", { class: "task-meta bad" }, "bloqueada: " + (t.reason || "sem motivo escrito")) : null,
        t.verify ? h("div", { class: "task-meta" }, "verificar: " + (Array.isArray(t.verify) ? t.verify.join("; ") : t.verify)) : null,
        t.commit ? h("div", { class: "task-meta" }, "commit " + t.commit) : null)));
  }
  function paintTaskList() {
    if (!state.detail) return;
    const scope = $("#drawer").open ? $("#drawer") : $("#main");
    $$("[data-tasks]", scope).forEach((box) => box.replaceChildren(...taskItems(state.detail)));
    $$("[data-task-filter]", scope).forEach((b) => setPressed(b, b.getAttribute("data-task-filter") === state.taskFilter));
  }
  function tasksPanel(d) {
    const counts = { all: d.tasks.length, open: d.tasks.filter((t) => !t.checked && !t.blocked).length,
                     done: d.tasks.filter((t) => t.checked).length, blocked: d.tasks.filter((t) => t.blocked).length };
    return h("section", { class: "panel" },
      h("div", { class: "sechead" }, h("h3", {}, `Tarefas ${d.spec.progress.checked}/${d.spec.progress.total}`)),
      h("div", { class: "segmented", role: "group", "aria-label": "Filtrar tarefas" },
        TASK_FILTERS.map((f) => h("button", { type: "button", class: "ghost small", "data-task-filter": f.key,
          "aria-pressed": state.taskFilter === f.key ? "true" : "false",
          on: { click: () => { state.taskFilter = f.key; paintTaskList(); } } }, `${f.label} ${counts[f.key]}`))),
      h("div", { class: "task-list", "data-tasks": "1" }, taskItems(d)));
  }
  function recordsPanel(d) {
    return h("section", { class: "panel" },
      h("h3", {}, "Registros"),
      d.timeline.length
        ? h("ol", { class: "timeline" }, d.timeline.map((r) => h("li", {}, h("b", {}, r.record),
            typeof r.value === "object" ? Object.entries(r.value).map(([k, v]) => `${k}: ${v}`).join(", ") : String(r.value))))
        : h("p", { class: "empty" }, "nenhum registro ainda"));
  }
  function sectionPanel(id, sec, n) {
    return h("section", { class: "panel", id: "sec-" + n },
      h("div", { class: "sechead" }, h("h3", {}, sec.heading),
        state.writable ? h("button", { type: "button", class: "ghost small", on: { click: () => sectionDialog(id, sec) } }, "editar") : null),
      sec.state === "absent" ? h("p", { class: "empty" }, "ausente") : markdown(sec.body));
  }
  function specDetail(d, opts) {
    const s = d.spec, page = !!opts.page;
    state.detail = d;
    if (page) state.pageDetail = d;
    const present = d.sections.filter((x) => x.state !== "absent");
    const missing = d.sections.filter((x) => x.state === "absent");
    const nav = neighbours(s.id);
    const open = state.writable && s.phase === "plans" && !s.approved;
    const head = h("header", { class: "detail-head" },
      h("div", { class: "detail-title" },
        h("h2", { id: page ? "spec-title" : "drawer-title" }, `${s.id} · ${s.title || "(sem título)"}`),
        h("p", { class: "meta", id: page ? "spec-sub" : "drawer-sub" },
          stagePill(s.stage), " ", h("span", {}, STAGE_PT[s.stage] || s.stage), " · ",
          h("span", {}, `${s.progress.checked}/${s.progress.total} tarefas`), " · ",
          h("span", { title: fmtDate(s.date) }, "aberta " + ageLabel(s.date)))),
      h("div", { class: "actions" },
        h("button", { type: "button", class: "ghost small", on: { click: () => copyCommand(s.id) } }, "copiar comando"),
        open ? h("button", { type: "button", class: "primary small", title: d.ready && !d.ready.ok ? "faltam seções: " + (d.ready.missing || []).join(", ") : "grava approved by: human",
          on: { click: () => approve(s.id) } }, "aprovar") : null,
        s.approved ? h("span", { class: "chip stage-approved" }, "aprovada") : null,
        state.writable ? h("button", { type: "button", class: "ghost small", on: { click: () => discoveryDialog(s.id) } }, "descoberta") : null,
        page ? h("a", { class: "ghost small", href: "#/board" }, "voltar") : null,
        page ? null : h("a", { class: "ghost small", href: "#/spec/" + encodeURIComponent(s.id), on: { click: () => closeDrawer() } }, "página inteira"),
        page ? null : h("button", { type: "button", class: "ghost small", id: "drawer-close", "data-fkey": "drawer-close", on: { click: closeDrawer } }, "fechar")),
    );
    const toc = page && present.length
      ? h("nav", { class: "toc", "aria-label": "Seções da spec" },
          h("h3", {}, "Seções"),
          h("ol", {}, present.map((sec, i) => h("li", {}, h("a", {
            href: "#sec-" + (i + 1),
            on: { click: (e) => { e.preventDefault(); const box = $("#sec-" + (i + 1)); if (box) box.scrollIntoView({ block: "start" }); } },
          }, sec.heading)))))
      : null;
    const body = h("div", { class: "detail" },
      h("div", { class: "detail-main" },
        toc,
        present.map((sec, i) => sectionPanel(s.id, sec, i + 1)),
        missing.length ? h("p", { class: "empty" }, `faltam ${missing.length} seções: ${missing.map((x) => x.heading).join(", ")}`) : null),
      h("aside", { class: "detail-side" }, tasksPanel(d), recordsPanel(d)));
    const foot = h("footer", { class: "detail-foot" },
      nav.prev ? h("a", { href: "#/spec/" + encodeURIComponent(nav.prev.id), "data-open": nav.prev.id,
        on: { click: (e) => { if (!page) { e.preventDefault(); openDrawer(nav.prev.id, null); } } } }, `← ${nav.prev.id} · ${nav.prev.title || ""}`) : h("span"),
      nav.next ? h("a", { href: "#/spec/" + encodeURIComponent(nav.next.id), "data-open": nav.next.id,
        on: { click: (e) => { if (!page) { e.preventDefault(); openDrawer(nav.next.id, null); } } } }, `${nav.next.id} · ${nav.next.title || ""} →`) : h("span"));
    return h("article", { class: page ? "spec-page" : "spec-drawer" }, head, body, foot);
  }

  let drawerOpener = null;
  function closeDrawer() { const d = $("#drawer"); if (d.open) d.close(); }
  async function openDrawer(id, opener) {
    if (isRemoteId(id)) return;
    drawerOpener = opener || document.activeElement;
    const d = $("#drawer");
    /* Open first, then read: the click always answers, even when the store is slow. */
    d.replaceChildren(h("div", { class: "loading drawer-loading", role: "status", "aria-live": "polite" },
      h("p", { class: "loading-line" }, h("i", { class: "dot pulse", "aria-hidden": "true" }),
        `Lendo a spec ${id}…`)));
    if (!d.open) d.showModal();
    let det;
    try { det = await api("specs/" + encodeURIComponent(id)); } catch (e) { if (d.open) d.close(); fail(e); return; }
    d.replaceChildren(specDetail(det, { page: false }));
    const close = $("#drawer-close");
    if (close) close.focus();
    remember(id);
  }
  $("#drawer").addEventListener("close", () => {
    $("#drawer").replaceChildren();
    state.detail = state.pageDetail || null;
    if (drawerOpener && drawerOpener.isConnected) drawerOpener.focus();
    drawerOpener = null;
  });
  $("#drawer").addEventListener("click", (e) => { if (e.target === $("#drawer")) $("#drawer").close(); });

  async function viewDetail(id) {
    let d;
    try { d = await api("specs/" + encodeURIComponent(id)); }
    catch (e) { return emptyState("Spec não encontrada", `Nenhuma spec ${id} neste store.`, h("a", { class: "ghost", href: "#/board" }, "Voltar ao board")); }
    return specDetail(d, { page: true });
  }
  async function approve(id) {
    if (!state.writable) { toast("O portal está em somente leitura."); return; }
    if (!confirm(`Aprovar a spec ${id}? Isto grava “approved by: human”.`)) return;
    try { await api("approve", { body: { spec: id } }); toast("Aprovada por: human", true, { label: "Ver a fila", run: () => go("human") }); logEvent("action", `Spec #${id} aprovada`); }
    catch (e) { fail(e); return; }
    await load(true);
    await render();
  }

  /* ── the human queue as triage ───────────────────────────────────────────── */
  function humanRow(id, title, stage, extra, label) {
    const btn = id && !isRemoteId(id) ? h("button", { type: "button", class: "link", on: { click: (e) => openDrawer(id, e.currentTarget) } }, `${id} · ${title}`) : h("span", {}, `${id || label || "?"} · ${title}`);
    return h("div", { class: "triage-main" }, btn, " ", stagePill(stage), extra);
  }
  function ageOf(id) { const s = specById(id); return s ? s.date : ""; }
  function triageRow(o) {
    return h("li", { class: "triage", "data-spec": o.id || o.label || "", tabindex: "-1" },
      humanRow(o.id, o.title, o.stage, h("span", { class: "age", title: o.date ? fmtDate(o.date) : "" }, ageLabel(o.date)), o.label),
      h("p", { class: "why" }, o.why),
      o.summary ? h("p", { class: "summary" }, o.summary) : null,
      h("div", { class: "triage-actions" },
        o.id && !isRemoteId(o.id) ? h("button", { type: "button", class: "ghost small", on: { click: (e) => openDrawer(o.id, e.currentTarget) } }, "abrir") : null,
        o.id && !isRemoteId(o.id) ? h("button", { type: "button", class: "ghost small", on: { click: () => copyCommand(o.id) } }, "copiar comando") : null,
        o.id && !isRemoteId(o.id) ? h("a", { class: "ghost small", href: "#/spec/" + encodeURIComponent(o.id) }, "ver a spec") : null,
        o.id && isRemoteId(o.id) ? h("span", { class: "meta" }, "item de outro repositório; abra-o no store de origem") : null,
        o.approve ? h("button", { type: "button", class: "primary small", on: { click: () => approve(o.id) } }, "aprovar") : null));
  }
  function viewHuman() {
    const hu = state.human;
    if (!hu) return h("p", { class: "empty" }, "A fila não pôde ser lida.");
    if (!hu.count) {
      return emptyState("Nada espera por você", "Nenhum membro de épico aberto, tarefa bloqueada ou spec pronta sem aprovação.",
        h("a", { class: "ghost", href: "#/board" }, "Ver o board"));
    }
    const byId = (id) => (id ? specById(id) : null);
    const members = hu.epicMembers.slice().sort((a, b) => String(ageOf(a.id) || "9999").localeCompare(String(ageOf(b.id) || "9999")));
    const blocked = hu.blocked.slice().sort((a, b) => String(ageOf(a.id) || "9999").localeCompare(String(ageOf(b.id) || "9999")));
    const waiting = hu.awaitingApproval.slice().sort((a, b) => String(ageOf(a.id) || "9999").localeCompare(String(ageOf(b.id) || "9999")));
    const section = (title, hint, rows) => h("section", { class: "panel" },
      h("div", { class: "sechead" }, h("h2", {}, title), h("span", { class: "meta" }, `${rows.length}`)),
      hint ? h("p", { class: "meta" }, hint) : null,
      rows.length ? h("ul", { class: "triagelist" }, rows) : h("p", { class: "empty" }, "nada aqui"));
    return [
      h("section", { class: "hero", "aria-label": "Resumo da fila" },
        h("div", { class: "hero-num" }, h("strong", {}, String(hu.count)), h("span", {}, hu.count === 1 ? "spec espera por você" : "specs esperam por você")),
        h("div", { class: "hero-break" },
          h("span", { class: "chip" }, `${hu.epicMembers.length} em épicos`),
          h("span", { class: "chip" }, `${hu.blocked.length} bloqueadas`),
          h("span", { class: "chip" }, `${hu.awaitingApproval.length} aguardando aprovação`))),
      section("Membros de épicos abertos", "Itens de épicos marcados como decisão do humano.", members.map((m) => {
        const s = byId(m.id);
        return triageRow({ id: m.id, title: m.title, stage: m.stage, label: m.label, date: ageOf(m.id),
          summary: s ? s.summary : "", why: `membro do épico ${m.epic} · ${m.label} · ${m.status}`,
          approve: false });
      })),
      section("Tarefas bloqueadas", "O motivo está escrito na tarefa; abra a spec para ver o resto.", blocked.map((b) =>
        triageRow({ id: b.id, title: b.title, stage: b.stage, date: ageOf(b.id),
          summary: (specById(b.id) || {}).summary || "",
          why: blockedWhy(b.id) }))),
      section("Prontas, aguardando aprovação", "Aprovar grava um registro write-once; a linha some no próximo carregamento.", waiting.map((w) =>
        triageRow({ id: w.id, title: w.title, stage: w.stage, date: ageOf(w.id),
          summary: (specById(w.id) || {}).summary || "",
          why: "pronta e sem o registro approved", approve: state.writable }))),
    ];
  }

  /* ── epics: programme, health, next step ─────────────────────────────────── */
  function ring(done, total) {
    const r = 18, c = 2 * Math.PI * r;
    const pct = total ? done / total : 0;
    return svg("svg", { class: "ring", viewBox: "0 0 44 44", role: "img", "aria-label": `${done} de ${total} membros concluídos` },
      svg("circle", { cx: 22, cy: 22, r, class: "ring-track" }),
      svg("circle", { cx: 22, cy: 22, r, class: "ring-fill", "stroke-dasharray": String(c), "stroke-dashoffset": String(c * (1 - pct)), transform: "rotate(-90 22 22)" }),
      svg("text", { x: 22, y: 26, class: "ring-text", "text-anchor": "middle" }, total ? `${done}/${total}` : "—"));
  }
  function epicHealth(e) {
    if (e.cycle) return { key: "bad", label: "ciclo", why: "as dependências formam um ciclo" };
    if (e.items.some((i) => ["unmerged", "missing", "unreadable", "unverified"].includes(i.status))) return { key: "bad", label: "entrega irregular", why: "há membro não mesclado, ausente ou ilegível" };
    if (e.groups.some((g) => g.blocked)) return { key: "warn", label: "bloqueio", why: "há membro com tarefa bloqueada" };
    if (e.total && e.done === e.total) return { key: "ok", label: "concluído", why: "todos os membros arquivados" };
    if (e.items.some((i) => i.status === "in-progress")) return { key: "info", label: "em andamento", why: "há membro em execução" };
    return { key: "neutral", label: "planejado", why: "nenhum membro começou" };
  }
  function epicOpen(id) { return prefsJSON("epic." + id, {}); }
  function waveOpen(e, g) {
    const map = epicOpen(e.id), key = String(g.group);
    if (key in map) return !!map[key];
    return !(g.total > 0 && g.done === g.total);
  }
  function toggleWave(e, g) {
    const map = epicOpen(e.id);
    map[String(g.group)] = !waveOpen(e, g);
    prefsSet("epic." + e.id, JSON.stringify(map));
    render();
  }
  function epicItem(it, critical) {
    const on = critical.includes(it.label);
    const remote = !!it.spec && isRemoteId(it.spec);
    return h("li", { class: "wave-item" + (on ? " critical" : "") + (remote ? " remote" : ""), "data-label": it.label },
      h("div", { class: "wave-title" }, h("span", { class: "label" }, it.label), " ",
        it.spec && !isRemoteId(it.spec) ? h("a", { href: "#/spec/" + encodeURIComponent(it.spec), "data-open": it.spec,
          on: { click: (ev) => { ev.preventDefault(); openDrawer(it.spec, ev.currentTarget); } } }, it.title) : h("span", {}, it.title),
        remote ? h("span", { class: "chip", title: "este item vive em outro repositório; abra-o no store de origem" }, "fora deste repositório") : null),
      h("div", { class: "meta" }, h("span", { class: "chip st-" + it.status }, it.status), on ? h("span", { class: "chip crit" }, "caminho crítico") : null),
      it.after.length ? h("div", { class: "meta" }, "depois de " + it.after.join(", ") + (it.waitingOn.length ? " · esperando " + it.waitingOn.join(", ") : "")) : null);
  }
  function nextStep(e) {
    const flat = e.groups.flatMap((g) => e.items.filter((i) => i.group === g.group));
    const ready = flat.find((i) => i.status === "ready" || i.status === "in-progress");
    if (ready) return h("p", { class: "next-step" }, h("strong", {}, "Próximo passo: "), `${ready.label} · ${ready.title}`);
    const waiting = flat.find((i) => i.status === "waiting");
    if (waiting) return h("p", { class: "next-step" }, h("strong", {}, "Próximo passo: "), `${waiting.label} espera ${waiting.waitingOn.join(", ") || "uma dependência"}`);
    return h("p", { class: "next-step" }, h("strong", {}, "Próximo passo: "), e.total && e.done === e.total ? "nada pendente." : "nenhum item pronto; confira as dependências.");
  }
  function viewEpics() {
    if (!state.epics.supported) return emptyState("Nenhum épico ainda", "Um épico é uma spec com workItemType: epic que lista as filhas em ## Tasks.");
    return state.epics.epics.map((e) => {
      const health = epicHealth(e);
      return h("section", { class: "panel epic" },
        h("div", { class: "epic-head" },
          ring(e.done, e.total),
          h("div", { class: "epic-title" },
            h("h2", {}, h("a", { href: "#/spec/" + encodeURIComponent(e.id) }, `${e.id} · ${e.title}`)),
            h("div", { class: "meta" },
              h("span", { class: "chip health-" + health.key, title: health.why }, health.label),
              h("span", {}, `${e.total} membros`),
              h("span", {}, `${e.done} concluídos`))),
          h("div", { class: "epic-side" }, nextStep(e))),
        e.cycle ? h("p", { class: "bad", role: "alert" }, "Ciclo de dependência: " + e.cycle.join(" → ") + ". Corrija os after:; nenhum caminho crítico é desenhado.") : null,
        e.criticalPath.length ? h("p", { class: "meta" }, "Caminho crítico: " + e.criticalPath.join(" → ")) : null,
        e.items.length
          ? h("div", { class: "waves" }, e.groups.map((g) => {
              const open = waveOpen(e, g);
              const bodyId = `wave-${e.id}-${g.group}`;
              return h("section", { class: "wave" + (open ? "" : " is-closed") },
                h("h3", {},
                  h("button", { type: "button", class: "wave-toggle", "aria-expanded": open ? "true" : "false", "aria-controls": bodyId,
                    on: { click: () => toggleWave(e, g) } },
                    h("span", {}, `Onda ${g.group}${g.title ? " · " + g.title : ""}`),
                    h("span", { class: "meta" }, `${g.done}/${g.total}`))),
                h("ul", { id: bodyId, hidden: !open }, e.items.filter((it) => it.group === g.group).map((it) => epicItem(it, e.criticalPath))));
            }))
          : e.children.length
            ? h("ul", {}, e.children.map((c) => h("li", {}, h("a", { href: "#/spec/" + encodeURIComponent(c.id) }, `${c.id} · ${c.title}`), " ", stagePill(c.stage))))
            : h("p", { class: "empty" }, "nenhuma filha ainda"));
    });
  }

  /* ── gantt: the server derives dates, the page only draws and offers a table ── */
  function seedGantt(query) {
    if (!["zoom", "deps", "crit"].some((k) => query.has(k))) return;
    state.gantt.zoom = GANTT_ZOOMS[query.get("zoom")] ? query.get("zoom") : "week";
    state.gantt.deps = query.get("deps") !== "0";
    state.gantt.crit = query.get("crit") !== "0";
  }
  function ganttHash() {
    const g = state.gantt, p = new URLSearchParams();
    if (g.zoom !== "week") p.set("zoom", g.zoom);
    if (!g.deps) p.set("deps", "0");
    if (!g.crit) p.set("crit", "0");
    const qs = p.toString();
    return "#/gantt" + (qs ? "?" + qs : "");
  }
  function syncGanttURL() {
    const hash = ganttHash();
    if (location.hash !== hash) history.replaceState(null, "", hash);
  }
  function ticksFor(lo, hi, zoom) {
    const out = [];
    if (zoom === "day") {
      for (let d = Math.ceil(lo); d <= hi; d++) out.push({ d, label: String(new Date(d * DAY).getUTCDate()) });
    } else if (zoom === "week") {
      for (let d = Math.ceil(lo); d <= hi; d += 7) {
        const dt = new Date(d * DAY);
        out.push({ d, label: `${pad(dt.getUTCDate())}/${pad(dt.getUTCMonth() + 1)}` });
      }
    } else {
      const d0 = new Date(lo * DAY);
      let t = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth(), 1) / DAY;
      while (t < lo) {
        const d = new Date(t * DAY);
        t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1) / DAY;
      }
      while (t <= hi) {
        out.push({ d: t, label: isoOf(t).slice(0, 7) });
        const dd = new Date(t * DAY);
        t = Date.UTC(dd.getUTCFullYear(), dd.getUTCMonth() + 1, 1) / DAY;
      }
    }
    return out;
  }
  async function viewGantt() {
    let data;
    try { data = await api("schedule"); } catch (e) { fail(e); return h("p", { class: "empty" }, "O cronograma não pôde ser lido."); }
    const g = state.gantt;
    const root = h("div", { class: "gantt" });
    const setZoom = (z) => {
      const box = $(".gantt-scroll");
      if (box && g._lo != null) g.centerDay = g._lo + (box.scrollLeft + box.clientWidth / 2 - 200) / g._scale;
      g.zoom = z; syncGanttURL(); render();
    };
    const switchTo = (key, on) => { g[key] = on; syncGanttURL(); render(); };
    const controls = h("div", { class: "gantt-controls", role: "group", "aria-label": "Controles do Gantt" },
      h("div", { class: "segmented" }, Object.keys(GANTT_ZOOMS).map((z) =>
        h("button", { type: "button", class: "ghost small", "aria-pressed": g.zoom === z ? "true" : "false", on: { click: () => setZoom(z) } },
          { day: "Dia", week: "Semana", month: "Mês" }[z]))),
      h("button", { type: "button", class: "ghost small", on: { click: () => {
        const box = $(".gantt-scroll");
        if (!box || g._x == null) return;
        const left = g._x(Math.floor(Date.now() / DAY)) - box.clientWidth / 2;
        const behavior = matchMedia("(prefers-reduced-motion: no-preference)").matches ? "smooth" : "auto";
        box.scrollTo({ left: Math.max(0, left), behavior });
      } } }, "Hoje"),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: g.deps, on: { change: (e) => switchTo("deps", e.target.checked) } }), "dependências"),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: g.crit, on: { change: (e) => switchTo("crit", e.target.checked) } }), "caminho crítico"),
      h("span", { class: "meta grow" }, `${data.nodes.length} specs · ${data.criticalPath.length} no caminho crítico`));
    root.append(controls);
    root.append(h("ul", { class: "legend", "aria-label": "Legenda do Gantt" },
      ["captured", "ready", "approved", "executing", "blocked", "review", "archived"].map((st) =>
        h("li", {}, h("span", { class: "swatch stage-" + st, "aria-hidden": "true" }), st)),
      h("li", {}, h("span", { class: "swatch critical", "aria-hidden": "true" }), "caminho crítico"),
      h("li", {}, h("span", { class: "swatch slip", "aria-hidden": "true" }), "folga (+Nd)")));
    if (data.cycle) {
      root.append(h("p", { class: "gantt-alert", role: "alert" }, "Ciclo de dependência: " + data.cycle.join(" → ") + ". Corrija os dependsOn; nenhum cronograma é desenhado até lá."));
      return root;
    }
    const placed = data.nodes.filter((n) => n.earliestStart && n.earliestFinish);
    const undated = data.nodes.filter((n) => !(n.earliestStart && n.earliestFinish));
    if (!data.nodes.length) {
      root.append(emptyState("Nada para agendar", "Nenhuma spec aberta declara dependência, então não há cronograma."));
      return root;
    }
    if (placed.length) {
      const today = Math.floor(Date.now() / DAY);
      const spans = placed.map((n) => { const a = dayOf(n.earliestStart); return [n, a, Math.max(dayOf(n.earliestFinish), a + 1)]; });
      const lo = Math.min(today, ...spans.map((s) => s[1])) - 3;
      const hi = Math.max(today, ...spans.map((s) => s[2])) + 3;
      const scale = GANTT_ZOOMS[g.zoom] || 9, LABEL = 200, ROW = 30, TOP = 30;
      const W = Math.max(640, (hi - lo) * scale);
      const x = (d) => LABEL + (d - lo) * scale;
      g._lo = lo; g._scale = scale; g._x = x;
      const row = new Map(spans.map((s, i) => [s[0].id, i]));
      const el = svg("svg", { class: "gantt-svg", width: LABEL + W + 24, height: TOP + spans.length * ROW + 10, role: "img",
        "aria-label": `Gantt de ${spans.length} specs, de ${isoOf(lo)} a ${isoOf(hi)}. A tabela abaixo tem o mesmo dado.` });
      el.append(svg("defs", {}, svg("marker", { id: "gantt-arrow", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 6, markerHeight: 6, orient: "auto" },
        svg("path", { d: "M0,0 L8,4 L0,8 z", class: "gantt-arrowhead" }))));
      for (const t of ticksFor(lo, hi, g.zoom)) {
        el.append(svg("line", { class: "gantt-grid", x1: x(t.d), x2: x(t.d), y1: TOP - 6, y2: TOP + spans.length * ROW }),
          svg("text", { class: "gantt-tick", x: x(t.d) + 3, y: 16 }, t.label));
      }
      if (g.deps) {
        for (const [n, a] of spans) {
          const y = TOP + row.get(n.id) * ROW;
          for (const dep of n.dependsOn || []) {
            const di = row.get(dep);
            if (di == null) continue;
            const ds = spans[di], x1 = x(ds[2]), y1 = TOP + di * ROW + ROW / 2, x2 = x(a), y2 = y + ROW / 2;
            el.append(svg("path", { class: "gantt-dep" + (g.crit && n.critical && ds[0].critical ? " critical" : ""), "marker-end": "url(#gantt-arrow)",
              d: `M${x1},${y1} H${x1 + 8} V${y2} H${Math.max(x2 - 2, x1 + 8)}` }));
          }
        }
      }
      for (const [n, a, b] of spans) {
        const y = TOP + row.get(n.id) * ROW, w = Math.max(x(b) - x(a), 6);
        const label = `${n.id} ${n.title || "(sem título)"}, ${n.earliestStart} a ${n.earliestFinish}`
          + (n.end ? `, fim declarado em ${n.end}` : "") + (g.crit && n.critical ? ", caminho crítico" : "") + (n.slipDays ? `, folga de ${n.slipDays} dias` : "");
        el.append(svg("a", { href: "#/spec/" + encodeURIComponent(n.id), class: "gantt-bar", "aria-label": label },
          svg("title", {}, label),
          svg("text", { class: "gantt-label", x: 4, y: y + ROW / 2 + 4 }, `${n.id} · ${(n.title || "").slice(0, 26)}`),
          svg("rect", { class: "gantt-rect st-" + n.stage + (g.crit && n.critical ? " critical" : "") + (n.slipDays ? " slip" : ""), x: x(a), y: y + 5, width: w, height: ROW - 10, rx: 3 }),
          g.crit && n.critical ? svg("text", { class: "gantt-chip", x: x(a) + 4, y: y + ROW / 2 + 4 }, "crítico") : null,
          n.slipDays ? svg("text", { class: "gantt-slip", x: x(a) + w + 4, y: y + ROW / 2 + 4 }, "+" + n.slipDays + "d") : null));
      }
      if (today >= lo && today <= hi) {
        el.append(svg("line", { class: "gantt-today", x1: x(today), x2: x(today), y1: TOP - 6, y2: TOP + spans.length * ROW }),
          svg("text", { class: "gantt-todaylabel", x: x(today) + 3, y: TOP + spans.length * ROW + 6 }, "hoje"));
      }
      const scroller = h("div", { class: "gantt-scroll", tabindex: "0", role: "region", "aria-label": "Gráfico de Gantt, rolável na horizontal" }, el);
      scroller.addEventListener("scroll", () => { g.scrollLeft = scroller.scrollLeft; });
      root.append(scroller);
      requestAnimationFrame(() => {
        if (!scroller.isConnected) return;
        if (g.centerDay != null) { scroller.scrollLeft = x(g.centerDay) - scroller.clientWidth / 2; g.centerDay = null; }
        else scroller.scrollLeft = g.scrollLeft || 0;
      });
      root.append(h("details", { class: "gantt-table" },
        h("summary", {}, "Ver o cronograma como tabela"),
        h("table", {},
          h("caption", {}, `Cronograma derivado de ${spans.length} specs`),
          h("thead", {}, h("tr", {}, ["Spec", "Início", "Fim", "Fim declarado", "Depende de", "Folga", "Caminho crítico", "Estágio"].map((t) => h("th", { scope: "col" }, t)))),
          h("tbody", {}, spans.map(([n]) => h("tr", {},
            h("td", {}, h("a", { href: "#/spec/" + encodeURIComponent(n.id) }, `${n.id} · ${n.title || ""}`)),
            h("td", {}, fmtDate(n.earliestStart)), h("td", {}, fmtDate(n.earliestFinish)),
            h("td", {}, n.end ? fmtDate(n.end) : "—"),
            h("td", {}, (n.dependsOn || []).join(", ") || "—"),
            h("td", {}, n.slipDays ? `+${n.slipDays} d` : "0"),
            h("td", {}, n.critical ? "sim" : "não"),
            h("td", {}, n.stage)))))));
    }
    if (undated.length) {
      root.append(h("h3", {}, "Sem datas"), h("ul", { class: "gantt-undated" }, undated.map((n) =>
        h("li", {}, h("a", { href: "#/spec/" + encodeURIComponent(n.id) }, `${n.id} · ${n.title || "(sem título)"}`), " ", stagePill(n.stage),
          n.dependsOn && n.dependsOn.length ? h("span", { class: "meta" }, " depois de " + n.dependsOn.join(", ")) : null))));
    }
    if (!placed.length && !undated.length) root.append(emptyState("Nada para agendar", "As specs têm dependências, mas nenhuma data derivável."));
    return root;
  }

  /* ── sessions and chat: the same visual vocabulary ───────────────────────── */
  function isWaiting(s) { return s.state === "waiting" || s.status === "waiting"; }
  function sessionLabel(s) {
    const w = s.worktree;
    if (w && w.branch) return w.branch + (w.main ? " (main checkout)" : "");
    return (s.cwd || "").split("/").filter(Boolean).pop() || s.sessionId;
  }
  function since(iso) {
    if (!iso) return "";
    const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
    return m < 1 ? "agora" : m < 60 ? m + " min atrás" : Math.floor(m / 60) + " h atrás";
  }
  function radar(activity) {
    const peak = Math.max(1, ...activity);
    const total = activity.reduce((a, b) => a + b, 0);
    return h("div", { class: "radar", role: "img", "aria-label": `${total} eventos nos últimos ${activity.length} minutos` },
      activity.map((n) => h("i", { class: n ? "on" : "", style: `height:${n ? Math.max(12, Math.round(100 * n / peak)) : 4}%` })));
  }
  function subLane(sa, winStart, winMs) {
    const at = (iso, fallback) => Math.min(1, Math.max(0, ((iso ? Date.parse(iso) : fallback) - winStart) / winMs));
    const from = at(sa.start, winStart), to = at(sa.end, winStart + winMs);
    return h("div", { class: "sublane" },
      h("span", { class: "sublabel" }, sa.agentType || "sub-agente", sa.active ? h("span", { class: "chip st-in-progress" }, "ativo") : null),
      h("div", { class: "track", role: "img", "aria-label": `${sa.agentType || "sub-agente"} de ${sa.start || "?"} a ${sa.end || "agora"}` },
        h("i", { class: sa.active ? "span active" : "span", style: `left:${(100 * from).toFixed(1)}%;width:${Math.max(0.8, 100 * (to - from)).toFixed(1)}%` })));
  }
  function viewSessions() {
    const ss = state.sessions;
    if (!ss) return h("p", { class: "empty" }, "As sessões ao vivo não puderam ser lidas.");
    const waiting = ss.sessions.filter(isWaiting);
    const winMs = ss.windowMinutes * 60000;
    const winStart = Date.parse(ss.generatedAt) - winMs;
    return [
      h("section", { class: "panel" + (waiting.length ? " waiting" : "") },
        h("div", { class: "sechead" }, h("h2", {}, "Esperando você"), h("span", { class: "meta" }, `${waiting.length}`)),
        waiting.length ? h("ul", { class: "humanlist" }, waiting.map((s) => h("li", {},
          h("strong", {}, sessionLabel(s)), " ", h("span", { class: "meta" }, "desde " + since(s.statusSince || s.lastEvent)),
          s.pendingTool ? h("span", { class: "chip" }, s.pendingTool) : null,
          h("blockquote", { class: "question" }, s.pendingQuestion || "(esperando, sem texto de pergunta)"))))
          : h("p", { class: "empty" }, "nenhuma sessão esperando por você")),
      h("section", { class: "panel" },
        h("div", { class: "sechead" }, h("h2", {}, `Sessões · últimos ${ss.windowMinutes} min`), h("span", { class: "meta" }, `${ss.sessions.length}`)),
        ss.sessions.length ? ss.sessions.map((s) => h("div", { class: "lane" + (isWaiting(s) ? " hot" : "") },
          h("div", { class: "lanehead" },
            h("strong", {}, sessionLabel(s)), " ", h("span", { class: "chip ss-" + s.state }, s.state),
            h("span", { class: "meta" }, "último evento " + (since(s.lastEvent) || "nunca"))),
          radar(s.activity || []),
          (s.subagents || []).map((sa) => subLane(sa, winStart, winMs))))
          : h("p", { class: "empty" }, "nenhuma sessão ao vivo nos worktrees deste repositório"))];
  }

  const CHAT_POLL_MS = 1500;
  const CHAT_DONE = ["done", "failed", "timeout"];
  function chatTrees() {
    const p = state.projects && state.projects.find((x) => x.name === state.project);
    return p ? p.worktrees || [] : [];
  }
  function chatLog() {
    const c = state.chat;
    const items = c.events.map((ev) =>
      ev.kind === "tool" ? h("li", { class: "chat-tool" }, "ferramenta: ", h("code", {}, ev.name))
        : ev.kind === "result" ? h("li", { class: "chat-result" + (ev.isError ? " bad" : "") }, h("strong", {}, "Resposta"), h("div", { class: "chat-text" }, ev.text || ""))
          : h("li", { class: "chat-text" }, ev.text || ""));
    const tail = [];
    if (c.job && !CHAT_DONE.includes(c.status)) tail.push(h("p", { class: "meta", role: "status" }, "rodando…"));
    if (c.status && c.status !== "running") tail.push(h("p", { class: "meta" }, "estado: " + c.status));
    if (c.error) tail.push(h("p", { class: "bad" }, c.error));
    if (c.cost != null) tail.push(h("p", { class: "meta" }, `custo: US$ ${Number(c.cost).toFixed(4)}`));
    return h("div", { id: "chat-log", "aria-live": "polite" }, items.length ? h("ol", { class: "chat-events" }, items) : null, tail);
  }
  function paintChatLog() {
    const old = $("#chat-log");
    if (old) old.replaceWith(chatLog());
    const send = $("#chat-send");
    if (send) send.disabled = !!state.chat.job && !CHAT_DONE.includes(state.chat.status);
  }
  async function pollChat(job) {
    const c = state.chat;
    while (c.job === job && !CHAT_DONE.includes(c.status)) {
      await new Promise((r) => setTimeout(r, CHAT_POLL_MS));
      if (c.job !== job) return;
      let r;
      try { r = await api(`chat/${encodeURIComponent(job)}?after=${c.next}`); }
      catch (e) { c.status = "failed"; c.error = e.message; paintChatLog(); return; }
      c.events.push(...(r.events || [])); c.next = r.next; c.status = r.status;
      c.error = r.error || ""; c.cost = r.costUsd;
      paintChatLog();
    }
  }
  async function askChat(e) {
    e.preventDefault();
    const c = state.chat;
    const question = c.draft.trim();
    if (!question) return;
    const body = { question };
    if (c.worktree) body.worktree = c.worktree;
    let r;
    try { r = await api("chat", { body }); } catch (err) { fail(err); return; }
    Object.assign(c, { job: r.job, status: "running", events: [], next: 0, cost: null, error: "" });
    paintChatLog();
    pollChat(r.job);
  }
  function viewChat() {
    const c = state.chat;
    const trees = chatTrees();
    if (c.worktree && !trees.some((w) => w.path === c.worktree)) c.worktree = "";
    return h("section", { class: "panel chat" },
      h("div", { class: "sechead" }, h("h2", {}, "Chat"),
        h("span", { class: "meta" }, "somente leitura (Read, Grep, Glob)" + (state.chatBudget != null ? ` · teto US$ ${state.chatBudget}` : ""))),
      h("form", { class: "chat-form", on: { submit: askChat } },
        trees.length ? h("label", {}, "Worktree",
          h("select", { on: { change: (ev) => { c.worktree = ev.target.value; } } },
            trees.map((w) => h("option", { value: w.main ? "" : w.path, selected: (w.main ? "" : w.path) === c.worktree, title: w.path },
              (w.branch || "(detached)") + (w.main ? " · main" : ""))))) : null,
        h("label", {}, "Pergunta",
          h("textarea", { maxLength: 4000, required: true, value: c.draft, on: { input: (ev) => { c.draft = ev.target.value; } } })),
        h("div", { class: "actions" }, h("button", { type: "submit", class: "primary", id: "chat-send" }, "Perguntar"))),
      chatLog());
  }

  /* ── search: facets and a safe highlight ─────────────────────────────────── */
  function highlighted(text, needle) {
    if (!needle) return [text];
    const out = [], low = String(text).toLowerCase(), n = needle.toLowerCase();
    let i = 0;
    while (i <= text.length) {
      const at = low.indexOf(n, i);
      if (at < 0) { out.push(text.slice(i)); break; }
      if (at > i) out.push(text.slice(i, at));
      out.push(h("mark", {}, text.slice(at, at + n.length)));
      i = at + n.length;
      if (!n) break;
    }
    return out;
  }
  async function viewSearch(q) {
    let r;
    try { r = await api("search?q=" + encodeURIComponent(q)); } catch (e) { fail(e); return h("p", {}, "A busca falhou."); }
    if (state.searchFor !== q) { state.searchFor = q; state.searchFacets = {}; }
    const facets = state.searchFacets || {};
    const enriched = r.results.map((x) => ({ ...x, meta: specById(x.id) || null }));
    const visible = enriched.filter((x) => !facets.stage || x.stage === facets.stage)
      .filter((x) => !facets.type || (x.meta && x.meta.workItemType) === facets.type)
      .filter((x) => !facets.subject || (x.meta && x.meta.subject) === facets.subject)
      .filter((x) => !facets.tag || (x.meta && (x.meta.tags || []).includes(facets.tag)));
    const values = (key) => {
      const set = new Set();
      enriched.forEach((x) => {
        if (key === "stage") { if (x.stage) set.add(x.stage); }
        else if (key === "tag") (x.meta && x.meta.tags || []).forEach((t) => set.add(t));
        else if (key === "type") { if (x.meta && x.meta.workItemType) set.add(x.meta.workItemType); }
        else if (key === "subject") { if (x.meta && x.meta.subject) set.add(x.meta.subject); }
      });
      return [...set].sort((a, b) => String(a).localeCompare(String(b), "pt-BR"));
    };
    const facetBar = (key, label) => values(key).length
      ? h("label", { class: "control" }, label,
          h("select", { value: facets[key] || "", on: { change: (e) => { state.searchFacets = { ...facets, [key]: e.target.value }; render(); } } },
            h("option", { value: "" }, "todos"),
            values(key).map((v) => h("option", { value: v, selected: facets[key] === v }, `${v} (${enriched.filter((x) => (key === "tag" ? (x.meta && x.meta.tags || []).includes(v) : key === "type" ? (x.meta && x.meta.workItemType) === v : key === "subject" ? (x.meta && x.meta.subject) === v : x.stage === v)).length})`))))
      : null;
    return h("section", { class: "panel" },
      h("div", { class: "sechead" }, h("h2", {}, `${visible.length} de ${r.count} resultado(s) para “${q}”`),
        h("button", { type: "button", class: "ghost small", on: { click: () => openPalette("search") } }, "nova busca")),
      h("div", { class: "tools-row" }, facetBar("stage", "Etapa"), facetBar("subject", "Assunto"), facetBar("tag", "Tag"), facetBar("type", "Tipo")),
      visible.length
        ? h("ul", { class: "results" }, visible.map((x) => h("li", { "data-spec": x.id, tabindex: "-1" },
            h("a", { href: "#/spec/" + encodeURIComponent(x.id) }, x.id, " · ", highlighted(x.title || "(sem título)", q)),
            " ", stagePill(x.stage),
            x.snippet ? h("div", { class: "meta" }, "…", highlighted(x.snippet, q), "…") : null)))
        : h("p", { class: "empty" }, "nenhum resultado neste recorte"));
  }

  /* ── projects: only `cq portal serve` answers /api/projects ──────────────── */
  async function loadProjects() {
    loadCollapsed();
    let r;
    try { r = await api("projects"); } catch (e) { return; }
    if (!r || !Array.isArray(r.projects) || !r.projects.length) return;
    state.projects = r.projects;
    const kept = prefsGet("project", prefsGet("qproject", ""));
    const chosen = r.projects.find((p) => p.name === kept) || r.projects[0];
    state.project = chosen.name; state.base = chosen.prefix;
    loadCollapsed();
    paintProjects();
  }
  function loadCollapsed() { state.board.collapsed = prefsJSON("collapsed." + (state.project || "local"), ["stage:archived"]); }
  function paintProjects() {
    const nav = $("#projects");
    const pick = (p) => ({ click: () => switchProject(p) });
    nav.replaceChildren(h("h2", {}, "Projetos"), h("ul", {}, state.projects.map((p) => h("li", {},
      h("button", { type: "button", class: "project", "aria-current": p.name === state.project ? "true" : "false", on: pick(p) }, p.name),
      h("ul", { class: "trees", "aria-label": "Worktrees de " + p.name }, (p.worktrees || []).map((w) => h("li", {},
        h("button", { type: "button", class: "tree", title: w.path, on: pick(p) },
          (w.branch || "(detached)") + (w.main ? " · main" : "")))))))));
    nav.hidden = false;
  }
  async function switchProject(p) {
    if (p.name === state.project) return;
    state.project = p.name; state.base = p.prefix;
    prefsSet("project", p.name);
    state.human = null; state.sessions = null;
    state.board = { group: "stage", sort: "rank", q: "", filters: [], hide: false, compact: false, collapsed: [] };
    loadCollapsed();
    Object.assign(state.chat, { worktree: "", job: null, status: "", events: [], next: 0, cost: null, error: "" });
    paintProjects();
    await loadConfig();
    try { await load(true); } catch (e) { fail(e); }
    if (location.hash && !/^#\/?(board)?(\?.*)?$/.test(location.hash)) location.hash = "#/board";
    else await render();
  }

  /* ── router ──────────────────────────────────────────────────────────────── */
  function parseRoute() {
    const raw = location.hash.replace(/^#\/?/, "");
    const qi = raw.indexOf("?");
    const path = qi < 0 ? raw : raw.slice(0, qi);
    const query = new URLSearchParams(qi < 0 ? "" : raw.slice(qi + 1));
    const parts = path.split("/");
    return { view: parts[0] || "board", arg: parts.slice(1).join("/"), query };
  }
  function paintNav(view) {
    $$("#nav a").forEach((a) => {
      if (!a.querySelector("svg.i")) a.prepend(icon(a.dataset.view));
      if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  function captureView() {
    const a = document.activeElement;
    const lanes = {};
    $$(".col-body[data-lane]").forEach((el) => { lanes[el.getAttribute("data-lane")] = el.scrollTop; });
    return {
      y: $("#main").scrollTop,
      key: a && a !== document.body ? a.getAttribute("data-fkey") : null,
      boardX: $(".board") ? $(".board").scrollLeft : 0,
      lanes,
    };
  }
  function restoreView(v) {
    if (!v) return;
    const main = $("#main");
    main.scrollTop = v.y;
    const b = $(".board");
    if (b) b.scrollLeft = v.boardX;
    if (v.lanes && main.classList.contains("is-board")) {
      $$(".col-body[data-lane]").forEach((el) => { el.scrollTop = v.lanes[el.getAttribute("data-lane")] || 0; });
    }
    if (v.key && (!document.activeElement || document.activeElement === document.body)) {
      const el = byKey(v.key);
      if (el) el.focus({ preventScroll: true });
    }
  }
  let renderSeq = 0;
  async function render() {
    const seq = ++renderSeq;
    closeEvents();
    const route = parseRoute();
    if (route.view === "board") seedBoard(route.query);
    if (route.view === "gantt") seedGantt(route.query);
    let content;
    const dataView = ["board", "list", "human", "epics", "gantt"].includes(route.view);
    if (dataView && state.loading && !state.specs.length) content = loadingView();
    else if (dataView && state.loadError && !state.specs.length) content = errorView();
    else if (route.view === "spec") content = await viewDetail(decodeURIComponent(route.arg || ""));
    else if (route.view === "search") content = await viewSearch(route.query.get("q") || decodeURIComponent(route.arg || ""));
    else if (route.view === "list") content = viewList();
    else if (route.view === "human") content = viewHuman();
    else if (route.view === "gantt") content = await viewGantt();
    else if (route.view === "epics") content = viewEpics();
    else if (route.view === "sessions") content = state.sessionsOn ? viewSessions()
      : emptyState("Sessões desligadas", "Este servidor não foi iniciado com --sessions.", h("a", { class: "ghost", href: "#/board" }, "Voltar ao board"));
    else if (route.view === "chat") content = state.chatOn ? viewChat()
      : emptyState("Chat desligado", "Este servidor não foi iniciado com --chat.", h("a", { class: "ghost", href: "#/board" }, "Voltar ao board"));
    else content = viewBoard();
    if (seq !== renderSeq) return;
    const before = captureView();
    paintNav(route.view);
    const main = $("#main");
    main.className = route.view === "board" ? "is-board" : "";
    main.replaceChildren(...nodes(content));
    restoreView(before);
    paintPulse();
    paintPause();
    paintPending();
    paintEvents();
  }

  /* ── ranking: the global order, one commit per reorder ───────────────────── */
  function viewList() {
    const plans = state.specs.filter((s) => s.phase === "plans").sort((a, b) => a.rank - b.rank);
    const canDrag = state.writable;
    let dragId = null;
    const rows = plans.map((s, idx) => h("tr", { draggable: canDrag, "data-id": s.id, "data-spec": s.id, tabindex: "-1",
      on: {
        dragstart: (e) => { dragId = s.id; e.currentTarget.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(s.id)); },
        dragend: (e) => e.currentTarget.classList.remove("dragging"),
        dragover: (e) => { if (dragId != null) { e.preventDefault(); e.currentTarget.classList.add("over"); } },
        dragleave: (e) => e.currentTarget.classList.remove("over"),
        drop: (e) => { e.preventDefault(); e.currentTarget.classList.remove("over"); if (dragId != null && dragId !== s.id) move(plans, dragId, s.id); dragId = null; },
      } },
      h("td", { class: "num" }, s.rank),
      h("td", { class: "spec-cell" }, h("a", { href: "#/spec/" + encodeURIComponent(s.id), "data-open": s.id, on: { click: (ev) => { ev.preventDefault(); openDrawer(s.id, ev.currentTarget); } } }, `${s.id} · ${s.title}`), cardMeta(s)),
      h("td", {}, stagePill(s.stage)),
      h("td", { class: "grip" },
        h("button", { type: "button", class: "ghost small", disabled: !canDrag || idx === 0, "aria-label": `Mover ${s.id} para cima`, on: { click: () => step(plans, s.id, -1) } }, "▲"),
        h("button", { type: "button", class: "ghost small", disabled: !canDrag || idx === plans.length - 1, "aria-label": `Mover ${s.id} para baixo`, on: { click: () => step(plans, s.id, 1) } }, "▼"))));
    return [h("div", { class: "panel" },
      h("div", { class: "sechead" }, h("h2", {}, "Ranking global"), h("span", { class: "meta" }, `${plans.length} specs em plans`)),
      h("p", { class: "meta" }, state.writable ? "Arraste uma linha, ou use as setas, para mudar a prioridade. Um reorder é um commit." : "Reordenar está desligado (somente leitura)."),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["#", "Spec", "Etapa", "Mover"].map((t) => h("th", { scope: "col" }, t)))),
        h("tbody", {}, rows))))];
  }
  async function move(plans, fromId, toId) {
    const order = plans.map((s) => s.id).filter((id) => String(id) !== String(fromId));
    order.splice(order.findIndex((id) => String(id) === String(toId)), 0, fromId);
    await reorder(order);
  }
  async function step(plans, id, delta) {
    const order = plans.map((s) => s.id), i = order.findIndex((x) => String(x) === String(id)), j = i + delta;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    await reorder(order, id);
  }
  async function reorder(order, focusId) {
    try {
      const r = await api("reorder", { body: { order } });
      toast(`Reordenado: ${r.written} spec(s) escritas${r.batched ? " em um commit" : ""}`, true);
      logEvent("action", `Ranking reordenado (${r.written} specs)`);
    } catch (e) { fail(e); }
    await load(true); await render();
    if (focusId != null) {
      const b = document.querySelector(`tr[data-id="${focusId}"] button:not(:disabled)`);
      if (b) b.focus();
    }
  }

  /* ── keyboard ────────────────────────────────────────────────────────────── */
  function typingIn(el) {
    return !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
  }
  function cursorItem() {
    const active = document.activeElement && document.activeElement.closest("[data-spec]");
    if (active) return active;
    return $("#main [data-spec].is-cursor") || null;
  }
  function moveCursor(delta) {
    const items = $$("#main [data-spec]");
    if (!items.length) return;
    let i = items.findIndex((el) => el.classList.contains("is-cursor"));
    if (i < 0) i = items.indexOf(document.activeElement && document.activeElement.closest("[data-spec]"));
    if (i < 0) i = delta > 0 ? -1 : items.length;
    i = Math.max(0, Math.min(items.length - 1, i + delta));
    items.forEach((el) => el.classList.remove("is-cursor"));
    items[i].classList.add("is-cursor");
    items[i].focus();
  }
  let gPending = 0;
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if ((e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === "k") {
      if (document.querySelector("dialog[open]")) return;
      e.preventDefault(); openPalette(); return;
    }
    if (e.key === "Escape") { closeEvents(); return; }
    if (typingIn(t) || document.querySelector("dialog[open]")) return;
    if (e.key === "/") { e.preventDefault(); openPalette("search"); return; }
    if (e.key === "?") { e.preventDefault(); openHelp(); return; }
    if (e.key === "t") { cycleTheme(); return; }
    if (e.key === "j" || e.key === "k") { e.preventDefault(); moveCursor(e.key === "j" ? 1 : -1); return; }
    const item = cursorItem();
    const id = item ? item.getAttribute("data-spec") : null;
    if (e.key === "Enter") {
      if (!id) return;
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) location.hash = "#/spec/" + encodeURIComponent(id);
      else openDrawer(id, item);
      return;
    }
    if (e.key === "a") {
      if (!id || !isAwaiting(id) || !state.writable) return;
      e.preventDefault();
      approve(id);
      return;
    }
    if (e.key === "g") { gPending = Date.now(); return; }
    if (gPending && Date.now() - gPending < 1000) {
      const map = { b: "board", l: "list", d: "human", e: "epics", g: "gantt", s: "sessions" };
      if (map[e.key]) { e.preventDefault(); gPending = 0; go(map[e.key]); }
    }
  });

  /* ── boot ────────────────────────────────────────────────────────────────── */
  applyTheme();
  state.paused = prefsGet("autorefresh", "1") === "0";
  paintPause();
  $("#palette-open").prepend(icon("search"));
  $("#help-open").replaceChildren(icon("help"));
  $("#capture").prepend(icon("plus"));
  $("#push").prepend(icon("up"));
  $("#refresh").prepend(icon("refresh"));
  $("#events-open").prepend(icon("bell"));
  if (navigator.platform && /Mac/i.test(navigator.platform)) $("#palette-key").textContent = "⌘ K";
  $("#palette-open").addEventListener("click", () => openPalette());
  $("#theme").addEventListener("click", cycleTheme);
  $("#help-open").addEventListener("click", openHelp);
  $("#pause").addEventListener("click", togglePause);
  $("#refresh").addEventListener("click", () => refresh(true));
  $("#events-open").addEventListener("click", toggleEvents);
  $("#capture").addEventListener("click", captureDialog);
  $("#push").addEventListener("click", pushNow);
  function routeLoading() {
    return h("div", { class: "loading route-loading", role: "status", "aria-live": "polite", "aria-busy": "true" },
      h("p", { class: "loading-line" }, h("i", { class: "dot pulse", "aria-hidden": "true" }), "Abrindo a visão…"));
  }
  function isRouteHash() {
    return location.hash === "" || location.hash === "#" || location.hash.startsWith("#/");
  }
  window.addEventListener("hashchange", () => {
    /* In-page anchors (#main, #sec-3) belong to the browser, not to the router. */
    if (!isRouteHash()) return;
    /* A slow route must not leave the previous screen looking like the new one. */
    if (!state.loading) $("#main").replaceChildren(routeLoading());
    render().then(() => $("#main").focus());
  });
  $(".skip").addEventListener("click", (e) => { e.preventDefault(); $("#main").focus(); });
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => { if (theme() === "auto") paintFavicon(); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
  setInterval(tickFreshness, 1000);
  /* A slow store deserves a slower poll: the interval follows the last load, so a 7 s
     snapshot does not turn into a permanent queue of overlapping requests. */
  function schedulePoll() {
    const delay = Math.min(60000, Math.max(8000, state.lastLoadMs * 2));
    setTimeout(async () => {
      if (!state.paused && !document.hidden && !$("#palette").open && !$("#drawer").open) {
        await refresh(false);
      }
      schedulePoll();
    }, delay);
  }
  schedulePoll();

  (async () => {
    render();
    await loadProjects();
    /* Config and the first snapshot are independent reads: run them together so the
       first paint is not the sum of two seven-second store builds. */
    const firstLoad = load(false).then((value) => ({ value }))
      .catch((error) => { state.loading = false; state.loadError = error && error.message || "falha"; return { error }; });
    const config = loadConfig();
    const [, loaded] = await Promise.all([config, firstLoad]);
    if (loaded.error) fail(loaded.error);
    if (state.sessionsOn) await loadSessions();
    await render();
    paintNotify();
    await render();
  })();
})();
