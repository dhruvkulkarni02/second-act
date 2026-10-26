// Second Act frontend: plays a session's SSE stream and renders it for the chosen
// audience, language and club. Every cue and briefing variant arrives from the server,
// so switching settings re-renders instantly without new requests.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const prefs = loadPrefs();
  const state = freshState();
  let source = null;
  let ltTimer = null;
  let toastTimer = null;
  const signalValues = {};

  function freshState() {
    return {
      sessionId: null, meta: null, offline: true, score: {}, clock: "0:00", period: 0,
      phase: "prematch", events: [], cues: [], predictions: [], briefing: { status: "pending", variants: [] },
      recap: { status: "pending", variants: [] }, briefingInfo: {}, recapInfo: {}, ended: false,
      seenCueKeys: new Set(),
    };
  }

  function loadPrefs() {
    const base = { audience: "analyst", language: "en", focus: "", speed: "30", match: "m-001" };
    try { return { ...base, ...JSON.parse(localStorage.getItem("second-act-prefs") || "{}") }; }
    catch { return base; }
  }
  function savePrefs() {
    try { localStorage.setItem("second-act-prefs", JSON.stringify(prefs)); } catch { /* optional */ }
  }
  const t = (key) => (UI[prefs.language] || UI.en)[key] || UI.en[key] || key;

  // ----- interface text -----
  function applyLabels() {
    document.documentElement.lang = prefs.language;
    document.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
    $("start").textContent = state.sessionId ? t("restart") : t("start");
    renderPhase();
    renderAgentsBadge();
  }

  // ----- pitch -----
  function drawStripes() {
    const g = $("stripes");
    for (let i = 0; i < 10; i += 2) {
      const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      r.setAttribute("x", (i * 10.5).toFixed(1)); r.setAttribute("y", "0");
      r.setAttribute("width", "10.5"); r.setAttribute("height", "68");
      g.appendChild(r);
    }
  }
  function teamColour(code) {
    if (!state.meta) return "#fff";
    return code === state.meta.home.code ? state.meta.home.primary_colour : state.meta.away.primary_colour;
  }
  function svg(tag, attrs) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }
  function drawDirection() {
    const g = $("direction");
    g.innerHTML = "";
    if (!state.meta || !state.period) return;
    const toHigh = state.meta.attacking_toward_high_x[state.period];
    const other = toHigh === state.meta.home.code ? state.meta.away.code : state.meta.home.code;
    const label = (code, x, anchor) => {
      const text = svg("text", { x, y: -1.3, "text-anchor": anchor, class: "dir" });
      text.textContent = `${code} →`;
      if (anchor === "start") text.textContent = `← ${code}`;
      text.setAttribute("fill", teamColour(code));
      g.appendChild(text);
    };
    label(toHigh, 104, "end");
    label(other, 1, "start");
    g.setAttribute("class", "dir");
  }
  function drawTrail() {
    const g = $("trail");
    g.innerHTML = "";
    const recent = state.events.filter((e) => e.x !== undefined && e.x !== null).slice(-14);
    recent.forEach((e, i) => {
      const age = (recent.length - i) / recent.length;
      const opacity = Math.max(0.15, 1 - age * 0.85);
      const colour = teamColour(e.team);
      if (["pass", "carry", "shot"].includes(e.type) && e.end_x !== undefined && e.end_x !== null) {
        const line = svg("line", { x1: e.x, y1: e.y, x2: e.end_x, y2: e.end_y, stroke: colour,
          opacity, class: `ev ${e.type}` });
        if (e.type === "shot") { line.setAttribute("marker-end", "url(#arrow)"); line.style.color = colour; }
        g.appendChild(line);
      } else if (["tackle", "interception", "pressure", "foul", "clearance"].includes(e.type)) {
        g.appendChild(svg("circle", { cx: e.x, cy: e.y, r: e.type === "pressure" ? 0.6 : 0.9,
          fill: colour, opacity: opacity * 0.9, class: "ev dot" }));
      }
    });
    const last = recent[recent.length - 1];
    if (last) {
      const bx = last.end_x ?? last.x, by = last.end_y ?? last.y;
      $("ball").setAttribute("cx", bx); $("ball").setAttribute("cy", by);
    }
  }
  function goalFlash(e) {
    const circle = svg("circle", { cx: e.x ?? 52.5, cy: e.y ?? 34, r: 2, class: "goal-flash" });
    $("trail").appendChild(circle);
  }

  // ----- header -----
  function renderHeader() {
    if (!state.meta) return;
    const { home, away } = state.meta;
    $("home-name").textContent = home.short_name; $("away-name").textContent = away.short_name;
    document.documentElement.style.setProperty("--home", home.primary_colour);
    document.documentElement.style.setProperty("--away", away.primary_colour);
    $("home-score").textContent = state.score[home.code] ?? 0;
    $("away-score").textContent = state.score[away.code] ?? 0;
    $("clock").textContent = state.clock;
    renderPhase();
  }
  function renderPhase() {
    $("phase").textContent = t(state.phase);
  }
  function renderAgentsBadge() {
    if (!state.sessionId) { $("agents").textContent = ""; return; }
    $("agents").innerHTML = "";
    const badge = el("span", `badge ${state.offline ? "" : "online"}`, state.offline ? t("offline_badge") : t("online_badge"));
    $("agents").appendChild(badge);
  }
  function setStatus(key) { $("status").textContent = t(key); }

  // ----- variant selection -----
  const focus = () => prefs.focus || null;
  const matches = (v) => v.audience === prefs.audience && v.language === prefs.language && (v.club_focus ?? null) === focus();
  function currentVariant(collection) {
    const exact = collection.variants.find(matches);
    if (exact) return { variant: exact, fallback: false };
    const english = collection.variants.find((v) => v.audience === "analyst" && v.language === "en" && (v.club_focus ?? null) === focus())
      || collection.variants.find((v) => v.audience === "analyst" && v.language === "en");
    return { variant: english || null, fallback: Boolean(english) };
  }

  // ----- evidence chips -----
  function chips(ids) {
    const wrap = el("div", "evidence");
    if (prefs.audience !== "analyst" || !ids || !ids.length) return wrap;
    ids.forEach((id) => {
      const chip = el("button", "chip");
      chip.type = "button";
      chip.title = t("evidence");
      const value = signalValues[id];
      chip.innerHTML = "";
      chip.append(document.createTextNode(id.replace(/^sig-/, "")));
      if (value) {
        const b = el("b", "", ` = ${formatValue(value)}`);
        chip.append(b);
      }
      chip.addEventListener("click", () => fetchSignals([id]).then(() => rerenderOverlays()));
      wrap.appendChild(chip);
    });
    return wrap;
  }
  function formatValue(sig) {
    let v = String(sig.value);
    if (prefs.language === "es") v = v.replace(".", ",");
    const unit = { pct: "%", m: " m", kmh: " km/h", per_window: "/5′", per_min: "/min", xg: " xG" }[sig.unit] || "";
    return v + unit;
  }
  async function fetchSignals(ids) {
    const missing = [...new Set(ids)].filter((id) => !(id in signalValues));
    if (!missing.length || !state.sessionId) return;
    for (let i = 0; i < missing.length; i += 150) {
      const batch = missing.slice(i, i + 150);
      try {
        const res = await fetch(`/api/sessions/${state.sessionId}/signals?ids=${encodeURIComponent(batch.join(","))}`);
        const data = await res.json();
        Object.assign(signalValues, data.signals || {});
      } catch { /* chips stay without values */ }
    }
  }

  // ----- lower third and feed -----
  function cueTeam(cue) {
    if (!state.meta) return null;
    const text = `${cue.headline} ${cue.body}`;
    const home = state.meta.home.short_name, away = state.meta.away.short_name;
    const iHome = text.indexOf(home), iAway = text.indexOf(away);
    if (iHome < 0 && iAway < 0) return null;
    if (iAway < 0 || (iHome >= 0 && iHome < iAway)) return state.meta.home.code;
    return state.meta.away.code;
  }
  function showLowerThird(cue) {
    const lt = $("lower-third");
    lt.dataset.kind = cue.kind;
    lt.dataset.status = cue.status || "";
    const team = cue.kind === "prediction_check" ? null : cueTeam(cue);
    lt.style.setProperty("--team", team ? teamColour(team) : "var(--accent)");
    lt.dataset.team = team ? "yes" : "";
    $("lt-kind").textContent = cue.kind === "prediction_check" ? t(cue.status || "prediction_check") : t(cue.kind);
    $("lt-headline").textContent = cue.headline;
    $("lt-text").textContent = cue.body;
    const ev = $("lt-evidence");
    ev.innerHTML = "";
    if (prefs.audience === "analyst") {
      fetchSignals(cue.evidence).then(() => { ev.innerHTML = ""; ev.appendChild(chips(cue.evidence)); });
    }
    lt.hidden = false;
    lt.style.animation = "none"; void lt.offsetWidth; lt.style.animation = "";
    clearTimeout(ltTimer);
    ltTimer = setTimeout(() => { lt.hidden = true; }, Math.max(4, cue.duration_s) * 1000);
  }
  function renderFeed() {
    const list = $("feed");
    list.innerHTML = "";
    const visible = state.cues.filter((c) => matches(c) && !["halftime_briefing", "fulltime_recap"].includes(c.kind));
    $("cue-count").textContent = `${visible.length} ${t("cues")}`;
    if (!visible.length) { list.appendChild(el("li", "empty", "—")); return; }
    visible.slice().reverse().forEach((cue) => {
      const li = el("li", `${cue.kind} ${cue.status || ""}`);
      const team = cue.kind === "prediction_check" ? null : cueTeam(cue);
      if (team) li.style.setProperty("--team", teamColour(team));
      li.appendChild(el("span", "t", cue.match_time));
      const body = el("div");
      body.appendChild(el("div", "h", cue.headline));
      body.appendChild(el("div", "b", cue.body));
      li.appendChild(body);
      list.appendChild(li);
    });
  }

  // ----- predictions panel -----
  function predictionTexts() {
    const { variant } = currentVariant(state.briefing);
    const claims = {};
    if (variant) variant.briefing.teams.forEach((tm) => tm.predictions.forEach((p) => { claims[p.prediction_id] = p.claim; }));
    const explain = {};
    state.cues.filter((c) => c.kind === "prediction_check" && matches(c)).forEach((c) => { explain[c.prediction_id] = c.body; });
    return { claims, explain };
  }
  function renderPredictions(flashId) {
    const list = $("predictions");
    list.innerHTML = "";
    if (!state.predictions.length) { list.appendChild(el("li", "empty", t("predictions_empty"))); return; }
    const { claims, explain } = predictionTexts();
    const order = state.predictions.slice().sort((a, b) => (a.team === focus() ? -1 : 0) - (b.team === focus() ? -1 : 0));
    order.forEach((p) => {
      const li = el("li", `${p.status}${p.prediction_id === flashId ? " flash" : ""}`);
      const head = el("div", "p-head");
      head.appendChild(el("span", `verdict ${p.status}`, t(p.status)));
      head.appendChild(el("span", "", `${teamName(p.team)} · ${p.prediction_id}`));
      li.appendChild(head);
      li.appendChild(el("div", "p-claim", claims[p.prediction_id] || p.claim));
      if (explain[p.prediction_id]) li.appendChild(el("div", "p-explain", explain[p.prediction_id]));
      if (prefs.audience === "analyst") li.appendChild(el("span", "test-spec", testSpec(p)));
      list.appendChild(li);
    });
  }
  function teamName(code) {
    if (!state.meta) return code;
    return code === state.meta.home.code ? state.meta.home.short_name : state.meta.away.short_name;
  }
  function testSpec(p) {
    const scope = p.scope && p.scope.channel ? ` [${p.scope.channel}]` : "";
    return `${t("test")}: ${p.subject_team} ${p.metric}${scope} ${t(p.direction)} ${t("by_at_least")} ${p.threshold_pct}% ${t("vs_first_half")} · ${t("after")} ${p.evaluate_after_minute}′`;
  }

  // ----- half-time and full-time screens -----
  function renderBriefing() {
    const { variant, fallback } = currentVariant(state.briefing);
    const progress = $("briefing-progress");
    const reads = $("team-reads");
    reads.innerHTML = "";
    if (!variant) {
      $("briefing-headline").textContent = t("halftime");
      $("briefing-meta").textContent = "";
      progress.hidden = state.briefing.status === "ready";
      progress.querySelector("span:last-child").textContent = t("briefing_in_progress");
      return;
    }
    progress.hidden = !fallback;
    progress.querySelector("span:last-child").textContent = t("translating");
    const b = variant.briefing;
    $("briefing-headline").textContent = b.headline;
    $("briefing-meta").textContent = metaLine(state.briefingInfo);
    b.teams.forEach((tm) => {
      const card = el("article", "team-read");
      card.style.setProperty("--team", teamColour(tm.team));
      card.appendChild(el("h3", "", teamFull(tm.team)));
      card.appendChild(section(t("working"), tm.working, "good"));
      card.appendChild(section(t("not_working"), tm.not_working, "bad"));
      const preds = el("div");
      preds.appendChild(el("h4", "", t("adjustments")));
      const ul = el("ul", "points pred");
      tm.predictions.forEach((p) => {
        const li = el("li", "", p.claim);
        if (prefs.audience === "analyst") li.appendChild(el("span", "test-spec", testSpec(p)));
        ul.appendChild(li);
      });
      preds.appendChild(ul);
      card.appendChild(preds);
      reads.appendChild(card);
    });
  }
  function teamFull(code) {
    if (!state.meta) return code;
    return code === state.meta.home.code ? state.meta.home.name : state.meta.away.name;
  }
  function section(title, points, cls) {
    const wrap = el("div");
    wrap.appendChild(el("h4", "", title));
    const ul = el("ul", `points ${cls}`);
    points.forEach((p) => {
      const li = el("li", "", p.text);
      li.appendChild(chips(p.evidence));
      ul.appendChild(li);
    });
    wrap.appendChild(ul);
    return wrap;
  }
  function metaLine(info) {
    if (!info || !info.source) return "";
    const templateLabel = info.offline && !info.fallback_reason ? t("source_offline") : t("source_template");
    const parts = [info.source === "model" ? t("source_model") : templateLabel];
    if (info.attempts) parts.push(`${info.attempts} ${t(info.attempts === 1 ? "attempt" : "attempts")}`);
    if (info.removed_claims && info.removed_claims.length) parts.push(`${info.removed_claims.length} ${t("removed")}`);
    return parts.join(" · ");
  }
  function renderRecap() {
    const { variant, fallback } = currentVariant(state.recap);
    const body = $("recap-body");
    body.innerHTML = "";
    const progress = $("recap-progress");
    if (!variant) {
      $("recap-headline").textContent = t("fulltime");
      progress.hidden = state.recap.status === "ready";
      return;
    }
    progress.hidden = !fallback;
    const r = variant.recap;
    $("recap-headline").textContent = r.headline;
    $("recap-meta").textContent = metaLine(state.recapInfo);
    body.appendChild(sectionWrap(t("first_half_story"), r.first_half, "pred"));
    body.appendChild(sectionWrap(t("changes"), r.changes, "pred"));
    const preds = el("section");
    preds.appendChild(el("h4", "", t("verdicts")));
    const ul = el("ul", "points pred");
    r.predictions.forEach((p) => {
      const li = el("li");
      li.appendChild(el("span", `verdict ${p.status}`, t(p.status)));
      li.appendChild(document.createTextNode(p.text));
      li.appendChild(chips(p.evidence));
      ul.appendChild(li);
    });
    preds.appendChild(ul);
    body.appendChild(preds);
    body.appendChild(sectionWrap(t("result"), r.result, "good"));
  }
  function sectionWrap(title, points, cls) {
    const s = el("section");
    s.appendChild(section(title, points, cls));
    return s;
  }
  function rerenderOverlays() {
    if (!$("briefing-overlay").hidden) renderBriefing();
    if (!$("recap-overlay").hidden) renderRecap();
  }
  function rerenderAll() {
    applyLabels();
    renderHeader();
    renderFeed();
    renderPredictions();
    rerenderOverlays();
  }

  // ----- stream -----
  function handle(kind, data) {
    switch (kind) {
      case "match":
        state.meta = data.metadata;
        fillFocusOptions();
        renderHeader();
        break;
      case "event":
        if (data.type === "period_start") {
          state.period = data.period;
          state.phase = data.period === 1 ? "first_half" : "second_half";
          if (data.period === 2) {
            $("briefing-overlay").hidden = true; $("resume").hidden = true; setStatus("status_live");
          }
          drawDirection();
        }
        if (data.type === "period_end" && data.period === 1) { state.phase = "halftime"; }
        state.events.push(data);
        if (state.events.length > 60) state.events.splice(0, state.events.length - 60);
        state.clock = `${data.minute}:${String(data.second).padStart(2, "0")}`;
        drawTrail();
        renderHeader();
        break;
      case "score":
        state.score = data.score;
        renderHeader();
        goalFlash(state.events[state.events.length - 1] || {});
        toast(`${t("goal")} · ${teamName(data.team)} · ${data.minute}′`);
        break;
      case "status":
        if (data.phase === "halftime" && data.hold_s) {
          $("resume").hidden = false;
          $("briefing-overlay").hidden = false;
          renderBriefing();
        } else if (data.phase === "halftime") {
          state.phase = "halftime";
          setStatus("status_ht");
          state.briefing.status = "in_progress";
          $("briefing-overlay").hidden = false;
          renderBriefing();
        } else if (data.phase === "fulltime") {
          state.phase = "fulltime";
          setStatus("status_ft");
          state.recap.status = "in_progress";
          $("recap-overlay").hidden = false;
          renderRecap();
        }
        renderPhase();
        break;
      case "briefing":
        state.briefing = { status: data.status, variants: data.variants };
        if (data.source) { state.briefingInfo = data; setStatus("status_ht_ready"); }
        $("open-briefing").disabled = false;
        prefetchEvidence(data.variants.flatMap((v) => [
          ...v.briefing.headline_evidence,
          ...v.briefing.teams.flatMap((tm) => [...tm.working, ...tm.not_working].flatMap((p) => p.evidence)),
        ])).then(renderBriefing);
        renderBriefing();
        renderPredictions();
        break;
      case "predictions": {
        const before = Object.fromEntries(state.predictions.map((p) => [p.prediction_id, p.status]));
        state.predictions = data.predictions;
        const changed = state.predictions.find((p) => before[p.prediction_id] && before[p.prediction_id] !== p.status);
        renderPredictions(changed && changed.prediction_id);
        break;
      }
      case "cue":
        state.cues.push(data);
        if (matches(data)) {
          if (!["halftime_briefing", "fulltime_recap"].includes(data.kind)) showLowerThird(data);
          renderFeed();
          if (data.kind === "prediction_check") renderPredictions(data.prediction_id);
        }
        break;
      case "recap":
        state.recap = { status: data.status, variants: data.variants };
        state.recapInfo = data;
        $("open-recap").disabled = false;
        prefetchEvidence(data.variants.flatMap((v) => [
          ...v.recap.first_half, ...v.recap.changes, ...v.recap.result, ...v.recap.predictions,
        ].flatMap((p) => p.evidence))).then(renderRecap);
        renderRecap();
        break;
      case "end":
        state.ended = true;
        state.phase = "fulltime";
        setStatus("status_done");
        renderPhase();
        if (source) { source.close(); source = null; }
        break;
      case "error":
        $("status").textContent = `${t("error")}: ${data.message}`;
        break;
      default:
        break;
    }
  }
  function prefetchEvidence(ids) {
    return prefs.audience === "analyst" ? fetchSignals(ids) : Promise.resolve();
  }
  function toast(text) {
    const node = $("toast");
    node.textContent = text;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 3500);
  }

  async function start() {
    if (source) { source.close(); source = null; }
    const matchList = state.matchList;
    Object.assign(state, freshState());
    state.matchList = matchList;
    Object.keys(signalValues).forEach((k) => delete signalValues[k]);
    $("trail").innerHTML = "";
    $("lower-third").hidden = true;
    $("briefing-overlay").hidden = true;
    $("recap-overlay").hidden = true;
    $("open-briefing").disabled = true;
    $("open-recap").disabled = true;
    $("resume").hidden = true;
    renderFeed();
    renderPredictions();
    const res = await fetch("/api/sessions", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ match_id: prefs.match, speed: Number(prefs.speed) }),
    });
    if (!res.ok) { $("status").textContent = `${t("error")}: ${res.status}`; return; }
    const session = await res.json();
    state.sessionId = session.session_id;
    state.offline = session.offline;
    applyLabels();
    setStatus("status_live");
    source = new EventSource(`/api/sessions/${state.sessionId}/stream`);
    ["match", "event", "score", "status", "cue", "predictions", "briefing", "recap", "end", "error"].forEach((kind) => {
      source.addEventListener(kind, (msg) => handle(kind, JSON.parse(msg.data)));
    });
  }

  // ----- controls -----
  function fillFocusOptions() {
    const select = $("focus");
    const current = prefs.focus;
    select.innerHTML = "";
    const none = el("option", "", t("noclub")); none.value = ""; none.dataset.i18n = "noclub";
    select.appendChild(none);
    if (state.meta) {
      [state.meta.home, state.meta.away].forEach((tm) => {
        const opt = el("option", "", tm.name); opt.value = tm.code; select.appendChild(opt);
      });
    } else {
      [["HAR", "Harbour Town"], ["KES", "Kestrel Athletic"]].forEach(([code, name]) => {
        const opt = el("option", "", name); opt.value = code; select.appendChild(opt);
      });
    }
    select.value = current;
  }
  async function loadMatches() {
    try {
      const list = await (await fetch("/api/matches")).json();
      const select = $("match");
      select.innerHTML = "";
      list.forEach((m) => { const o = el("option", "", `${m.title}`); o.value = m.match_id; select.appendChild(o); });
      select.value = list.some((m) => m.match_id === prefs.match) ? prefs.match : (list[0] && list[0].match_id);
      prefs.match = select.value;
      state.matchList = list;
      previewTeams();
    } catch { /* stays empty */ }
  }
  // Before kickoff, show the selected match's clubs (from its title) instead of Home/Away.
  function previewTeams() {
    if (state.meta || !state.matchList) return;
    const m = state.matchList.find((x) => x.match_id === prefs.match);
    if (!m) return;
    const [home, away] = m.title.replace(/\s*\(.*\)\s*$/, "").split(" v ");
    $("home-name").textContent = m.home_name || (home || m.home).split(" ")[0];
    $("away-name").textContent = m.away_name || (away || m.away).split(" ")[0];
    if (m.home_colour) document.documentElement.style.setProperty("--home", m.home_colour);
    if (m.away_colour) document.documentElement.style.setProperty("--away", m.away_colour);
  }

  function bindControls() {
    ["audience", "language", "focus"].forEach((id) => {
      $(id).value = prefs[id];
      $(id).addEventListener("change", () => {
        prefs[id] = $(id).value; savePrefs();
        if (id === "audience" && prefs.audience === "analyst") {
          prefetchEvidence(state.cues.filter(matches).flatMap((c) => c.evidence)).then(rerenderOverlays);
        }
        rerenderAll();
      });
    });
    $("speed").value = prefs.speed;
    $("speed").addEventListener("change", async () => {
      prefs.speed = $("speed").value; savePrefs();
      if (state.sessionId && !state.ended) {
        await fetch(`/api/sessions/${state.sessionId}/speed`, {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ speed: Number(prefs.speed) }),
        });
      }
    });
    $("match").addEventListener("change", () => { prefs.match = $("match").value; savePrefs(); previewTeams(); });
    $("start").addEventListener("click", start);
    document.querySelectorAll("[data-close]").forEach((btn) => btn.addEventListener("click", () => { $(btn.dataset.close).hidden = true; }));
    $("resume").addEventListener("click", () => {
      $("resume").hidden = true;
      if (state.sessionId) fetch(`/api/sessions/${state.sessionId}/resume`, { method: "POST" });
    });
    $("open-briefing").addEventListener("click", () => { $("briefing-overlay").hidden = false; renderBriefing(); });
    $("open-recap").addEventListener("click", () => { $("recap-overlay").hidden = false; renderRecap(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { $("briefing-overlay").hidden = true; $("recap-overlay").hidden = true; }
    });
  }

  drawStripes();
  fillFocusOptions();
  bindControls();
  applyLabels();
  loadMatches();
  renderFeed();
  renderPredictions();
})();
