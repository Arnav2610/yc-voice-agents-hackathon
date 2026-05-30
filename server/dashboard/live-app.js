"use strict";

const $ = (id) => document.getElementById(id);

function renderTranscriptLive(snap, events) {
  const el = $("transcript");
  el.innerHTML = ChronosUI.renderTranscriptHtml(snap, events, true);

  const finals = events.filter((e) => e.event_type === "final_transcript");
  const partials = events.filter((e) => e.event_type === "partial_transcript");
  const lastFinalTs = finals.length ? finals[finals.length - 1].timestamp_ms : 0;
  const lastPartial = partials.length ? partials[partials.length - 1] : null;
  if (lastPartial && lastPartial.timestamp_ms > lastFinalTs && lastPartial.data.text) {
    $("partial").innerHTML = `<div class="partial-bubble">🎙 ${ChronosUI.esc(lastPartial.data.text)}</div>`;
  } else {
    $("partial").innerHTML = "";
  }

  requestAnimationFrame(() => {
    const last = el.lastElementChild;
    if (last) last.scrollIntoView({ block: "end", behavior: "smooth" });
    else el.scrollTop = el.scrollHeight;
  });
}

function renderIncidentSop(inc, snap, isLive) {
  const panel = $("panel-sop");
  if (!isLive || !inc || !inc.incident_type) {
    $("incident").innerHTML = ChronosUI.renderIncidentCompactHtml(null);
    $("incident-progress").style.display = "none";
    panel.querySelectorAll(".handoff-ready, .handoff-pending").forEach((n) => n.remove());
    return;
  }
  inc._planDisplay = (snap.sop_plan && snap.sop_plan.protocol_title) || "";
  $("incident").innerHTML = ChronosUI.renderIncidentCompactHtml(inc);

  const prog = ChronosUI.checklistProgress(snap.checklist);
  const progEl = $("incident-progress");
  if (prog.total > 0) {
    progEl.style.display = "block";
    $("incident-progress-fill").style.width = prog.pct + "%";
    $("incident-progress-label").textContent = `${prog.done}/${prog.total} SOP items · ${prog.pct}%`;
  } else {
    progEl.style.display = "none";
  }

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
      `<div class="handoff-pending">📋 Gathering required info — ${(inc.missing_slots || []).length} item(s) remaining</div>`
    );
  }
}

function renderHero(snap, isLive) {
  const inc = snap.incident || {};
  const plan = snap.sop_plan || {};
  if (!isLive || !(inc.incident_type || inc.location_raw)) {
    $("proto-title").textContent = "Awaiting call…";
    $("incident-title").textContent = isLive ? "Listening" : "Idle";
    $("incident-badges").innerHTML = "";
    $("progress-wrap").style.display = "none";
    return;
  }
  $("proto-title").textContent = plan.protocol_title || ChronosUI.protocolTitle(inc.incident_type);
  $("incident-title").textContent = plan.display_name || ChronosUI.incidentLabel(inc.incident_type);
  $("incident-badges").innerHTML = [
    inc.incident_type ? `<span class="badge risk-${inc.risk_level || "unknown"}">${ChronosUI.esc(inc.risk_level || "unknown")} risk</span>` : "",
    inc.upgraded_to ? `<span class="badge risk-high">↑ ${ChronosUI.esc(inc.upgraded_to.replace(/_/g, " "))}</span>` : "",
    plan.source === "merged" ? `<span class="pill">AI-tailored</span>` : "",
  ]
    .filter(Boolean)
    .join("");

  const items = (snap.checklist || []).filter((c) => c.active);
  const done = items.filter((c) => c.resolved).length;
  const total = items.length;
  if (total > 0) {
    const pct = Math.round((done / total) * 100);
    $("progress-wrap").style.display = "block";
    $("progress-pct").textContent = `${done}/${total} · ${pct}%`;
    $("progress-fill").style.width = pct + "%";
  } else {
    $("progress-wrap").style.display = "none";
  }
}

function renderEscalation(snap, isLive) {
  const el = $("escalate");
  const inc = snap.incident || {};
  if (!isLive) {
    el.style.display = "none";
    return;
  }
  const missing = snap.missing_slot_labels || inc.missing_slots || [];
  const missingTxt =
    Array.isArray(missing) && missing.length
      ? missing.map((l) => (typeof l === "string" ? l : l.replace(/_/g, " "))).join(", ")
      : `${(inc.missing_slots || []).length} item(s)`;
  if (snap.human_handoff_ready) {
    el.style.display = "block";
    el.className = "handoff-ready";
    el.innerHTML = "⛑ Ready for human dispatcher handoff — all intake complete";
  } else if (inc.escalation_required && !snap.intake_complete) {
    el.style.display = "block";
    el.className = "handoff-pending";
    el.innerHTML = `📋 Stay on line — still need: <strong>${ChronosUI.esc(missingTxt)}</strong>`;
  } else {
    el.style.display = "none";
  }
}

function renderDispatchesLive(snap, isLive) {
  const panel = $("cad-panel");
  const board = $("cad-board");
  const disp = snap.dispatches || [];
  const hasDispatchEvent = (snap._dispatch_events || 0) > 0;

  if (!isLive || !disp.length || !hasDispatchEvent) {
    panel.style.display = "none";
    board.innerHTML = "";
    return;
  }

  const icons = { fire: "🚒", police: "🚔", ems: "🚑" };
  panel.style.display = "block";
  board.innerHTML = disp
    .map(
      (d) => `
    <div class="cad-unit">
      <div class="u-type">${icons[d.unit_type] || "📡"} ${ChronosUI.esc((d.unit_type || "").toUpperCase())}</div>
      <div class="u-status">● SIMULATED · EN ROUTE</div>
      <div class="u-loc">${ChronosUI.esc(d.location || "—")}</div>
      <div class="u-reason">${ChronosUI.esc(d.reason || "")}</div>
    </div>`
    )
    .join("");
}

function renderChecklist(snap, isLive) {
  const el = $("checklist");
  const plan = snap.sop_plan;
  const hint = $("checklist-hint");
  if (!isLive) {
    hint.textContent = "awaiting call";
    el.innerHTML = '<div class="empty">Start a call to begin SOP intake.</div>';
    return;
  }
  if (plan && plan.source === "merged") hint.textContent = "AI-tailored · live values";
  else if (plan && plan.protocol_title) hint.textContent = plan.protocol_title;
  else hint.textContent = "checklist + captured facts";

  el.innerHTML = ChronosUI.renderMergedIntakeHtml(snap, snap.recommended_slot);
  if (!el.innerHTML) {
    el.innerHTML = '<div class="empty">Classifying incident — intake will appear when type is detected.</div>';
  }
}

function renderNextQuestion(snap, isLive) {
  const el = $("next-question");
  const q = isLive ? snap.recommended_question : null;
  if (q) {
    el.textContent = "▶ " + q;
    el.className = "next-q-inline";
  } else {
    el.textContent = "—";
    el.className = "next-q-inline empty";
  }
}

function renderMemoryLive(snap) {
  $("memory").innerHTML = ChronosUI.renderMemoryHtml(snap);
}

async function tick() {
  try {
    const [latestResp, healthResp] = await Promise.all([
      fetch("/chronos/latest", { cache: "no-store" }),
      fetch("/chronos/health", { cache: "no-store" }),
    ]);
    if (!latestResp.ok) throw new Error("API " + latestResp.status);
    const d = await latestResp.json();
    const health = healthResp.ok ? await healthResp.json() : {};
    const snap = d.snapshot || {};
    const events = d.events || [];
    const lastTs = events.length ? events[events.length - 1].timestamp_ms : 0;
    const recent = lastTs && Date.now() - lastTs < 120000;
    const isLive = !!health.live_call || (recent && events.length > 0);
    snap._dispatch_events = events.filter((e) => e.event_type === "unit_dispatched").length;

    $("dot").className = "dot " + (isLive ? "live" : "idle");
    $("conn").textContent = isLive
      ? "live" + (d.source === "live_json" ? " · via bot" : "")
      : "idle — start a call at :7860";

    renderTranscriptLive(snap, events);
    renderHero(snap, isLive);
    renderEscalation(snap, isLive);
    renderIncidentSop(snap.incident, snap, isLive);
    renderNextQuestion(snap, isLive);
    renderChecklist(snap, isLive);
    renderDispatchesLive(snap, isLive);
    renderMemoryLive(snap);
  } catch (e) {
    $("dot").className = "dot";
    $("conn").textContent = "cannot reach dashboard API — restart: make dash";
    console.error("live tick failed", e);
  }
}

async function doReset() {
  const btn = $("reset-btn");
  btn.disabled = true;
  try {
    await fetch("/chronos/actions/reset", { method: "POST" });
  } catch (e) {}
  btn.disabled = false;
  tick();
}

setInterval(tick, 550);
tick();
