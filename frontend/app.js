(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const API = {
    state: "/api/experiment",
    start: "/api/experiment/start",
    reset: "/api/experiment/reset",
  };
  const ORDER = ["B1", "B2", "B3", "B4", "B5", "B6"];
  const POINTS = { B1: [160, 160], B2: [450, 160], B3: [740, 160], B4: [740, 370], B5: [450, 370], B6: [160, 370] };
  let poll;
  let compareVisible = false;
  let selectedMode = "paired";

  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const clamp = (value) => Math.max(0, Math.min(100, number(value)));
  const clock = (seconds) => `${String(Math.floor(number(seconds) / 60)).padStart(2, "0")}:${String(Math.floor(number(seconds) % 60)).padStart(2, "0")}`;
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
  const displayBay = (bayId) => bayId ? `BAY-${bayId.slice(1).padStart(2, "0")}` : null;

  async function request(url, options = {}) {
    const response = await fetch(url, { headers: { "Content-Type": "application/json" }, cache: "no-store", ...options });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || `Request failed (${response.status})`);
    return payload;
  }

  function connection(type, text) {
    $("#link").className = `link ${type}`;
    $("#link b").textContent = text;
  }

  function toast(message) {
    $("#toast").textContent = message;
    $("#toast").classList.add("show");
    window.setTimeout(() => $("#toast").classList.remove("show"), 3500);
  }

  function makeBays() {
    const positions = [[74, 76], [364, 76], [654, 76], [654, 286], [364, 286], [74, 286]];
    $("#bays").innerHTML = ORDER.map((id, index) => `<g class="bay" data-id="${id}" transform="translate(${positions[index]})">
      <rect width="172" height="120" rx="9"/><rect width="4" height="120" rx="2"/>
      <text class="badge" x="86" y="-8" text-anchor="middle">[NORMAL]</text>
      <text class="id" x="17" y="28">BAY ${id.slice(1).padStart(2, "0")}</text><text class="status" x="154" y="28" text-anchor="end">NOMINAL</text>
      <text class="temp" x="17" y="70">-- C</text><text class="rate" x="17" y="98">-- C/min</text></g>`).join("");
  }

  function routeFromEvents(events, current, next) {
    const arrived = events.filter((event) => event.type === "arrived" && event.bay_id).map((event) => event.bay_id);
    return [...arrived.slice(-5), current, next].filter(Boolean);
  }

  function renderMap(adaptive, fixed) {
    adaptive.bays.forEach((bay) => {
      const group = document.querySelector(`[data-id="${bay.id}"]`);
      if (!group) return;
      const state = bay.status === "critical" ? "confirmed" : bay.status === "alert" ? "alert" : bay.status === "watch" ? "suspected" : "nominal";
      group.setAttribute("class", `bay ${state}`);
      group.querySelector(".status").textContent = state === "nominal" ? "NOMINAL" : state.toUpperCase();
      group.querySelector(".badge").textContent = state === "confirmed" ? "[CONFIRMED]" : state === "alert" ? "[ALERT]" : state === "suspected" ? "[SUSPECTED]" : "[NORMAL]";
      group.querySelector(".temp").textContent = `${number(bay.temperature_c).toFixed(1)} C`;
      group.querySelector(".rate").textContent = `${number(bay.rise_rate_c_min) >= 0 ? "+" : ""}${number(bay.rise_rate_c_min).toFixed(2)} C/min`;
    });
    const current = adaptive.robot.current_bay_id;
    const next = adaptive.planner.next_station_id;
    const position = current && POINTS[current] ? POINTS[current] : [160, 250];
    $("#robot").setAttribute("transform", `translate(${position})`);
    const route = routeFromEvents(adaptive.events, current, next).map((id) => POINTS[id]).filter(Boolean);
    $("#route").setAttribute("points", route.map((point) => point.join(",")).join(" "));
    const fixedRoute = routeFromEvents(fixed.events, fixed.robot.current_bay_id, fixed.planner.next_station_id).map((id) => POINTS[id]).filter(Boolean);
    $("#fixedRoute").setAttribute("points", fixedRoute.map((point) => point.join(",")).join(" "));
  }

  function renderKpis(data) {
    const comparison = data.comparison;
    const adaptive = data.adaptive.kpis;
    const fixed = data.fixed.kpis;
    $("#lead").textContent = comparison.time_saved_s == null ? "--" : `${comparison.time_saved_s.toFixed(0)} s`;
    $("#leadNote").textContent = comparison.time_to_confirm_pct == null ? "Awaiting paired run" : `${comparison.time_to_confirm_pct.toFixed(1)}% earlier than fixed patrol`;
    $("#distance").textContent = `${adaptive.distance_m.toFixed(1)} m`;
    const saved = fixed.distance_m - adaptive.distance_m;
    $("#distanceNote").textContent = `${Math.abs(saved).toFixed(1)} m ${saved >= 0 ? "less" : "more"} than fixed route`;
    $("#unseen").textContent = `${adaptive.max_unseen_s.toFixed(0)} s`;
    $("#unseenNote").textContent = adaptive.guardrails_respected ? "Coverage guard respected" : "Review coverage guard";
    $("#inspected").textContent = adaptive.bays_inspected;
    $("#progressNote").textContent = data.status === "complete" ? "Full mission coverage" : "Inspection in progress";
  }

  function renderDecision(adaptive) {
    const decisions = adaptive.planner_decisions || [];
    const last = decisions[decisions.length - 1];
    const final = Boolean(adaptive.done);
    const activeAlerts = adaptive.planner.active_alerts || [];
    $("#decisionPanel").classList.toggle("complete", final);
    $("#decisionMode").textContent = final ? "FINAL DECISION" : activeAlerts.length ? "ALERT RESPONSE" : "PATROL DECISION";
    $("#decisionTitle").textContent = final ? "Mission closed" : activeAlerts.length ? "Active alert target" : "Next inspection";
    $("#decisionStep").textContent = final ? "✓" : String(Math.max(1, decisions.length)).padStart(2, "0");
    $("#next").textContent = displayBay(adaptive.planner.next_station_id) || (final ? "DONE" : "--");
    $("#eta").textContent = "--";
    $("#reason").textContent = final
      ? (last?.reason || "Mission complete: all six bays were inspected and final triage was locked.")
      : adaptive.planner.reason;
    $("#decisionMath").textContent = last?.reason || "Risk formula appears after the first planner decision.";
  }

  function renderTable(adaptive) {
    $("#bayRows").innerHTML = adaptive.bays.map((bay) => {
      const signal = bay.status === "critical" ? "critical" : bay.status === "alert" ? "alert" : bay.status === "watch" ? "suspected" : "nominal";
      const verdict = bay.status === "critical" ? "confirmed" : bay.status === "alert" ? "pending-alert" : bay.last_seen_s == null ? "pending" : "clear";
      return `<tr><td>${escapeHtml(bay.label)}</td><td><span class="chip ${signal}">${signal}</span></td>
        <td>${number(bay.temperature_c).toFixed(1)} C</td><td>${number(bay.rise_rate_c_min).toFixed(2)} C/min</td>
        <td>${bay.last_seen_s == null ? "Not visited" : `${number(bay.last_seen_s).toFixed(0)} s ago`}</td>
        <td><span class="chip ${verdict}">${verdict}</span></td></tr>`;
    }).join("");
  }

  function renderEvents(events) {
    const recent = [...events].reverse().slice(0, 12);
    $("#events").innerHTML = recent.length ? recent.map((event) => {
      const type = event.type === "hotspot_confirmed"
        ? "confirmed"
        : (event.type === "mission_completed" || event.type === "alert_resolved" || event.type === "false_alarm")
          ? "clear"
          : "alert";
      return `<li class="${type}"><div><b>${escapeHtml(event.type.replaceAll("_", " ").toUpperCase())}${event.bay_id ? ` / ${displayBay(event.bay_id)}` : ""}</b>
        <time>T+${clock(event.timestamp_s)}</time></div><p>${escapeHtml(event.message)}</p></li>`;
    }).join("") : '<li class="empty">O <span>No events yet.<small>Mission decisions appear here.</small></span></li>';
  }

  function render(data) {
    const adaptive = data.adaptive;
    const fixed = data.fixed;
    const primary = selectedMode === "fixed" ? fixed : adaptive;
    $("#loading").classList.add("hide");
    $("#run").textContent = `/ RUN ${data.run_id.slice(0, 8).toUpperCase()}`;
    $("#state").textContent = data.status;
    $("#orb").classList.toggle("active", data.status === "running");
    $("#elapsed").textContent = clock(primary.time_s);
    $("#coverage").textContent = `${Math.round(primary.kpis.coverage_pct)}%`;
    $("#bar").style.width = `${clamp(primary.kpis.coverage_pct)}%`;
    $("#battery").textContent = `${Math.round(primary.robot.battery_pct)}%`;
    $("#mode").textContent = data.status === "ready" ? "Standby" : data.status === "complete" ? "Complete" : "Autonomous";
    $("#velocity").textContent = data.status === "running" ? "1.0 m/s" : "0.0 m/s";
    $("#updated").textContent = `UPDATED ${new Date().toLocaleTimeString([], { hour12: false })}`;
    $("#start").disabled = data.status === "running";
    $("#reset").disabled = data.status === "running";
    renderKpis(data);
    renderMap(primary, fixed);
    renderDecision(primary);
    renderTable(primary);
    renderEvents(primary.events);
    clearTimeout(poll);
    if (data.status === "running") poll = window.setTimeout(refresh, 450);
  }

  async function refresh() {
    try {
      const data = await request(API.state);
      connection("online", "LIVE API");
      render(data);
    } catch (error) {
      connection("", "LINK LOST");
      toast(error.message);
      poll = window.setTimeout(refresh, 1800);
    }
  }

  async function start(mode = "paired") {
    selectedMode = mode;
    try { render(await request(API.start, { method: "POST", body: JSON.stringify({ mode }) })); }
    catch (error) { toast(error.message); }
  }

  async function startFixed() {
    return start("fixed");
  }

  async function reset() {
    selectedMode = "paired";
    try { render(await request(API.reset, { method: "POST", body: "{}" })); }
    catch (error) { toast(error.message); }
  }

  function setCompare(on) {
    compareVisible = on;
    $(".mapPanel").classList.toggle("compare-on", compareVisible);
    $("#compareToggle").setAttribute("aria-pressed", compareVisible ? "true" : "false");
    $("#compareToggle").textContent = compareVisible ? "Hide comparison" : "Show comparison";
  }

  makeBays();
  setCompare(false);
  $("#compareToggle").addEventListener("click", () => setCompare(!compareVisible));
  $("#start").addEventListener("click", () => start("paired"));
  $("#startFixed").addEventListener("click", startFixed);
  $("#reset").addEventListener("click", reset);
  refresh();
})();
