"use strict";

const $ = (id) => document.getElementById(id);

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

function renderTranscript(snapshot, events) {
  const el = $("transcript");
  el.innerHTML = ChronosUI.renderTranscriptHtml(snapshot, events, true);

  const partials = events.filter((e) => e.event_type === "partial_transcript");
  const partialEl = $("partial");
  if (partials.length) {
    const text = partials[partials.length - 1].data.text;
    partialEl.innerHTML = text ? `<div class="partial-bubble">🎙 ${ChronosUI.esc(text)}</div>` : "";
  } else {
    partialEl.innerHTML = "";
  }

  requestAnimationFrame(() => {
    ChronosUI.maybeAutoScrollTranscript(el, ChronosUI.transcriptMessageCount(snapshot, events));
  });
}

function renderIncident(inc, snap) {
  const panel = $("panel-sop");
  if (!inc || !inc.incident_type) {
    $("incident").innerHTML = ChronosUI.renderIncidentCompactHtml(null);
    $("incident-progress").style.display = "none";
    panel.querySelectorAll(".handoff-ready, .handoff-pending").forEach((n) => n.remove());
    return;
  }
  inc._planDisplay = (snap.sop_plan && snap.sop_plan.protocol_title) || "";
  $("incident").innerHTML = ChronosUI.renderIncidentCompactHtml(inc);

  const prog = ChronosUI.checklistProgress(snap);
  const progEl = $("incident-progress");
  if (prog.total > 0) {
    progEl.style.display = "block";
    $("incident-progress-fill").style.width = prog.pct + "%";
    $("incident-progress-label").textContent = `${prog.done}/${prog.total} SOP items · ${prog.pct}%`;
  } else progEl.style.display = "none";

  panel.querySelectorAll(".handoff-ready, .handoff-pending").forEach((n) => n.remove());
  const insertAfter = $("next-question");
  if (snap.human_handoff_ready) {
    insertAfter.insertAdjacentHTML(
      "afterend",
      `<div class="handoff-ready">⛑ Human dispatcher handoff — intake complete · ${ChronosUI.esc(inc.escalation_reason || "high-risk case")}</div>`
    );
  } else if (inc.escalation_required && !snap.intake_complete) {
    insertAfter.insertAdjacentHTML(
      "afterend",
      `<div class="handoff-pending">📋 Gathering required info before handoff — ${(inc.missing_slots || []).length} item(s) remaining</div>`
    );
  }
}

function renderDispatches(snapshot) {
  const el = $("dispatches");
  const panel = $("panel-dispatches");
  if (!el) return;
  const disp = snapshot.dispatches || [];
  if (!disp.length) {
    el.innerHTML = "";
    if (panel) panel.style.display = "none";
    return;
  }
  el.innerHTML = ChronosUI.renderDispatchAlertHtml(snapshot);
  if (panel) panel.style.display = "block";
}

function renderChecklist(snapshot, events) {
  const el = $("checklist");
  const plan = snapshot.sop_plan;
  const hint = $("checklist-hint");
  if (plan && plan.source === "merged") hint.textContent = "AI-tailored · values update live";
  else if (plan && plan.protocol_title) hint.textContent = plan.protocol_title + " · values update live";
  else hint.textContent = "checklist + captured facts";

  snapshot._events = events || [];
  el.innerHTML = ChronosUI.renderSopIntakeTable(snapshot, snapshot.recommended_slot);
  if (!el.innerHTML) el.innerHTML = '<div class="empty">Waiting for incident classification…</div>';
}

function renderMemory(snapshot) {
  $("memory").innerHTML = ChronosUI.renderMemoryHtml(snapshot);
}

function renderNextQuestion(snapshot) {
  const el = $("next-question");
  if (!el) return;
  const q = snapshot.recommended_question;
  if (q) {
    el.textContent = "▶ " + q;
    el.className = "next-q-inline";
  } else {
    el.textContent = "—";
    el.className = "next-q-inline empty";
  }
}

function renderEvents(events) {
  const el = $("events");
  const show = events.slice(-60);
  el.innerHTML = show
    .map((e) => {
      const t = new Date(e.timestamp_ms).toLocaleTimeString();
      let detail = "";
      const d = e.data || {};
      if (e.event_type === "incident_hypothesis") detail = `${d.incident_type} (${d.risk_level})`;
      else if (e.event_type === "safety_signal") detail = `tp=${d.third_party_risk} risk=${d.risk_level}`;
      else if (e.event_type === "memory_query") detail = (d.query || "").slice(0, 48);
      else if (e.event_type === "memory_result") detail = `${(d.results || []).length} hit(s)`;
      else if (e.event_type === "floor_action") detail = `${d.kind} — ${d.reason || ""}`.slice(0, 60);
      else if (e.event_type === "escalation_recommended") detail = d.reason || "";
      else if (e.event_type === "final_transcript") detail = (d.text || "").slice(0, 48);
      else if (e.event_type === "sop_checklist_update") detail = `next: ${d.recommended_slot || "—"}`;
      else if (e.event_type === "sop_plan_ready") detail = `plan: ${(d.sop_plan && d.sop_plan.protocol_title) || "ready"}`;
      else detail = JSON.stringify(d).slice(0, 48);
      return `<div class="ev ${ChronosUI.esc(e.event_type)}"><span class="t">${t}</span><span class="et">${ChronosUI.esc(
        e.event_type
      )}</span><span class="d">${ChronosUI.esc(detail)}</span></div>`;
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

  try {
    const pd = await getJSON("/chronos/policy-diff");
    $("patch-why").textContent = pd.patch?.why_this_fixes_it || "";
    $("patch-diff").innerHTML = colorizeDiff(pd.diff || "");
  } catch {}
}

function colorizeDiff(diff) {
  return ChronosUI.esc(diff)
    .split("\n")
    .map((l) => {
      if (l.startsWith("+")) return `<span class="add">${l}</span>`;
      if (l.startsWith("-")) return `<span class="del">${l}</span>`;
      if (l.startsWith("@@") || l.startsWith("---") || l.startsWith("+++")) return `<span class="hd">${l}</span>`;
      return l;
    })
    .join("\n");
}

async function loadScenarios() {
  try {
    const list = await getJSON("/chronos/scenarios");
    const sel = $("scenario-select");
    sel.innerHTML = list.map((s) => `<option value="${ChronosUI.esc(s.id)}">${ChronosUI.esc(s.title)}</option>`).join("");
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
    html += `<div class="cekura-row ${cls}"><span class="mk">${r.pass ? "✓" : "✗"}</span><span class="nm">${ChronosUI.esc(
      r.scenario
    )}</span><span class="nt">${ChronosUI.esc(r.note || "")}</span></div>`;
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
    renderIncident(snap.incident, snap);
    renderChecklist(snap, events);
    renderDispatches(snap);
    await ChronosUI.updateLiveMap(
      $("live-map-panel"),
      $("live-map-frame"),
      $("live-map-caption"),
      snap,
      !!(latest.events || []).length || !!health.live_call
    );
    renderNextQuestion(snap);
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
