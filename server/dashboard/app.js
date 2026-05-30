"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

function riskClass(r) {
  return "risk-" + (r || "unknown");
}

function renderTranscript(snapshot, events) {
  const el = $("transcript");
  const items = [];
  // Interleave caller turns, background speech, and agent guidance by event order.
  for (const ev of events) {
    if (ev.event_type === "final_transcript") items.push({ kind: "caller", text: ev.data.text });
    else if (ev.event_type === "background_speech")
      items.push({ kind: "background", text: ev.data.text });
    else if (ev.event_type === "agent_guidance") items.push({ kind: "agent", text: ev.data.text });
  }
  if (items.length === 0 && snapshot.turns) {
    for (const t of snapshot.turns) items.push({ kind: "caller", text: t });
  }
  el.innerHTML = items.map((i) => `<div class="turn ${i.kind}">${esc(i.text)}</div>`).join("");
  el.scrollTop = el.scrollHeight;

  const partials = events.filter((e) => e.event_type === "partial_transcript");
  $("partial").textContent = partials.length ? "… " + partials[partials.length - 1].data.text : "";
}

function renderIncident(inc) {
  if (!inc || !inc.incident_type) {
    $("incident").innerHTML = '<div class="k">incident</div><div class="v">listening…</div>';
    return;
  }
  const tp = inc.third_party_risk;
  const tpClass = tp === "active" ? "tp-active" : tp === "resolved" ? "tp-resolved" : "";
  const hazards = (inc.hazards || []).map((h) => `<span class="hazard">${esc(h)}</span>`).join("") || "—";
  let html = "";
  const row = (k, v) => (html += `<div class="k">${k}</div><div class="v">${v}</div>`);
  row("type", esc(inc.incident_type) + (inc.upgraded_to ? ` → ${esc(inc.upgraded_to)}` : ""));
  row("risk", `<span class="badge ${riskClass(inc.risk_level)}">${esc(inc.risk_level)}</span>`);
  row(
    "location",
    esc(inc.location_raw || "unknown") +
      (inc.location_needs_confirmation && inc.location_raw ? ' <span class="badge risk-medium">confirm</span>' : "")
  );
  row("caller safety", esc(inc.caller_safety));
  row("third-party risk", `<span class="${tpClass}">${esc(tp)}</span>`);
  row("hazards", hazards);
  $("incident").innerHTML = html;
  $("incident").insertAdjacentHTML(
    "afterend",
    ""
  );
  const exist = document.querySelector("#panel-incident .escalate");
  if (exist) exist.remove();
  if (inc.escalation_required) {
    $("panel-incident").insertAdjacentHTML(
      "beforeend",
      `<div class="escalate">⛑ Recommend human dispatcher — ${esc(inc.escalation_reason || "high-risk case")}</div>`
    );
  }
}

function renderChecklist(snapshot) {
  const el = $("checklist");
  const items = snapshot.checklist || [];
  const rec = snapshot.recommended_slot;
  el.innerHTML = items
    .filter((c) => c.active)
    .map((c) => {
      const cls = [c.resolved ? "resolved" : "", c.slot === rec ? "rec" : ""].join(" ");
      const mark = c.resolved ? "✓" : c.slot === rec ? "▶" : "○";
      return `<li class="${cls}"><span class="mark">${mark}</span><span class="q">${esc(c.question)}<div class="slot">${esc(c.slot)}</div></span></li>`;
    })
    .join("");
}

function renderMemory(snapshot) {
  const el = $("memory");
  const results = (snapshot.memory && snapshot.memory.results) || [];
  if (!results.length) {
    el.innerHTML = '<div class="mem"><div class="content">No memory retrieved yet.</div></div>';
    return;
  }
  el.innerHTML = results
    .map(
      (m) =>
        `<div class="mem"><span class="score">${(m.score ?? 0).toFixed(2)}</span><span class="type">${esc(
          m.memory_type
        )}</span><div class="content">${esc(m.content)}</div></div>`
    )
    .join("");
}

function renderEvents(events) {
  const el = $("events");
  const show = events.slice(-60);
  el.innerHTML = show
    .map((e) => {
      const t = new Date(e.timestamp_ms).toLocaleTimeString();
      let detail = "";
      const d = e.data || {};
      if (e.event_type === "incident_hypothesis") detail = `${d.incident_type} (${d.confidence})`;
      else if (e.event_type === "safety_signal") detail = `tp=${d.third_party_risk} risk=${d.risk_level}`;
      else if (e.event_type === "memory_query") detail = (d.query || "").slice(0, 48);
      else if (e.event_type === "memory_result") detail = `${(d.results || []).length} hit(s)`;
      else if (e.event_type === "floor_action") detail = `${d.kind} — ${d.reason || ""}`.slice(0, 60);
      else if (e.event_type === "escalation_recommended") detail = d.reason || "";
      else if (e.event_type === "final_transcript") detail = (d.text || "").slice(0, 48);
      else if (e.event_type === "sop_checklist_update") detail = `next: ${d.recommended_slot || "—"}`;
      else detail = JSON.stringify(d).slice(0, 48);
      return `<div class="ev ${esc(e.event_type)}"><span class="t">${t}</span><span class="et">${esc(
        e.event_type
      )}</span><span class="d">${esc(detail)}</span></div>`;
    })
    .join("");
  el.scrollTop = el.scrollHeight;
}

function fmtMetric(key, val) {
  if (val == null) return "—";
  if (key === "pass_rate") return Math.round(val * 100) + "%";
  return val;
}

const METRIC_ROWS = [
  ["pass_rate", "Pass rate"],
  ["missed_trapped_person_question", "Missed trapped-person Q"],
  ["wrong_branch_closure", "Wrong branch closure"],
  ["prior_memory_retrieved", "Prior memory retrieved"],
  ["avg_time_to_critical_guidance_ms", "Time→critical guidance (ms)"],
];

async function renderImprovement() {
  let imp;
  try {
    imp = await getJSON("/chronos/improvement");
  } catch {
    return;
  }
  const statusEl = $("improve-status");
  if (!imp || !imp.status) {
    statusEl.textContent = "No improvement run yet. Run `make improve` (or the Cekura loop).";
    statusEl.className = "improve-status";
    $("metrics").innerHTML = "";
    return;
  }
  statusEl.textContent =
    imp.status === "accepted"
      ? `✓ Patch ACCEPTED — ${imp.failure?.failure_type} fixed`
      : imp.status === "rejected"
      ? "✗ Patch rejected (no improvement / regression)"
      : imp.status;
  statusEl.className = "improve-status " + imp.status;

  const b = imp.before || {};
  const a = imp.after || {};
  let rows = '<tr><th>Metric</th><th>Before</th><th></th><th>After</th></tr>';
  for (const [k, label] of METRIC_ROWS) {
    rows += `<tr><td>${label}</td><td class="before">${fmtMetric(k, b[k])}</td><td class="arrow">→</td><td class="after">${fmtMetric(
      k,
      a[k]
    )}</td></tr>`;
  }
  $("metrics").innerHTML = rows;

  // Patch panel
  try {
    const pd = await getJSON("/chronos/policy-diff");
    $("patch-why").textContent = pd.patch?.why_this_fixes_it || "";
    $("patch-diff").innerHTML = colorizeDiff(pd.diff || "");
  } catch {}
}

function colorizeDiff(diff) {
  return esc(diff)
    .split("\n")
    .map((l) => {
      if (l.startsWith("+")) return `<span class="add">${l}</span>`;
      if (l.startsWith("-")) return `<span class="del">${l}</span>`;
      if (l.startsWith("@@") || l.startsWith("---") || l.startsWith("+++")) return `<span class="hd">${l}</span>`;
      return l;
    })
    .join("\n");
}

// --- demo control bar ---
async function loadScenarios() {
  try {
    const list = await getJSON("/chronos/scenarios");
    const sel = $("scenario-select");
    sel.innerHTML = list.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("");
  } catch {}
}

async function postAction(act, body) {
  const r = await fetch(`/chronos/actions/${act}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function setupControls() {
  document.querySelectorAll("#controls button[data-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      const body = act === "play" ? { scenario_id: $("scenario-select").value } : {};
      document.querySelectorAll("#controls button").forEach((b) => (b.disabled = true));
      try {
        await postAction(act, body);
      } catch {}
      pollJob();
    });
  });
}

async function pollJob() {
  try {
    const j = await getJSON("/chronos/job");
    const el = $("job-status");
    el.className = "job " + (j.status || "");
    el.textContent = j.action ? `${j.action}: ${j.status}${j.message ? " — " + j.message : ""}` : "";
    const busy = j.status === "running";
    document.querySelectorAll("#controls button").forEach((b) => (b.disabled = busy));
  } catch {}
}

async function renderCekura() {
  let c;
  try {
    c = await getJSON("/chronos/cekura");
  } catch {
    return;
  }
  const el = $("cekura");
  if (!c || !c.result_id) {
    el.innerHTML = '<div class="cekura-sub">No live Cekura run recorded yet.</div>';
    return;
  }
  const rate = Math.round((c.pass_rate || 0) * 100);
  let html = `<div class="cekura-head"><span class="cekura-rate">${c.met_expected_outcome}/${c.total}</span><span class="cekura-sub">Expected Outcome · ${rate}% · WebSocket run ${c.result_id}</span></div>`;
  for (const r of c.per_scenario || []) {
    const cls = r.pass ? "pass" : "fail";
    html += `<div class="cekura-row ${cls}"><span class="mk">${r.pass ? "✓" : "✗"}</span><span class="nm">${esc(
      r.scenario
    )}</span><span class="nt">${esc(r.note || "")}</span></div>`;
  }
  el.innerHTML = html;
}

async function tick() {
  try {
    const [latest, health] = await Promise.all([getJSON("/chronos/latest"), getJSON("/chronos/health")]);
    const snap = latest.snapshot || {};
    const events = latest.events || [];
    $("conn-dot").className = "dot " + (events.length ? "live" : "idle");
    $("conn-text").textContent = health.live_call ? `live: ${health.live_call}` : "idle";
    renderTranscript(snap, events);
    renderIncident(snap.incident);
    renderChecklist(snap);
    renderMemory(snap);
    renderEvents(events);
  } catch (e) {
    $("conn-dot").className = "dot";
    $("conn-text").textContent = "dashboard offline";
  }
}

setupControls();
loadScenarios();
setInterval(tick, 700);
setInterval(renderImprovement, 1500);
setInterval(pollJob, 1000);
setInterval(renderCekura, 3000);
tick();
renderImprovement();
pollJob();
renderCekura();
