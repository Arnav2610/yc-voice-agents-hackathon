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
    ChronosUI.maybeAutoScrollTranscript(el, ChronosUI.transcriptMessageCount(snap, events));
  });
}

function renderSopContext(inc, snap, isLive) {
  const panel = $("panel-sop-context");
  const hint = $("checklist-hint");

  if (!isLive) {
    hint.textContent = "awaiting call";
    $("incident").innerHTML = '<span class="chip chip-listening">Start a call to begin intake.</span>';
    $("incident-progress").style.display = "none";
    $("next-question").textContent = "—";
    $("next-question").className = "next-q-inline empty";
    panel.querySelectorAll(".handoff-ready, .handoff-pending").forEach((n) => n.remove());
    return;
  }

  const plan = snap.sop_plan || {};
  if (plan.source === "merged") hint.textContent = "AI-tailored · live values";
  else if (plan.protocol_title) hint.textContent = plan.protocol_title + " · live values";
  else hint.textContent = "live values";

  if (!inc || !inc.incident_type) {
    $("incident").innerHTML = ChronosUI.renderIncidentCompactHtml(null);
    $("incident-progress").style.display = "none";
  } else {
    inc._planDisplay = plan.protocol_title || "";
    $("incident").innerHTML = ChronosUI.renderIncidentCompactHtml(inc);

    const prog = ChronosUI.checklistProgress(snap);
    const progEl = $("incident-progress");
    if (prog.total > 0) {
      progEl.style.display = "block";
      $("incident-progress-fill").style.width = prog.pct + "%";
      $("incident-progress-label").textContent = `${prog.done}/${prog.total} SOP items · ${prog.pct}%`;
    } else {
      progEl.style.display = "none";
    }
  }

  const qEl = $("next-question");
  const q = snap.recommended_question;
  if (q) {
    qEl.textContent = "▶ " + q;
    qEl.className = "next-q-inline";
  } else {
    qEl.textContent = "—";
    qEl.className = "next-q-inline empty";
  }

  panel.querySelectorAll(".handoff-ready, .handoff-pending").forEach((n) => n.remove());
  const insertAfter = qEl;
  if (snap.human_handoff_ready) {
    insertAfter.insertAdjacentHTML(
      "afterend",
      `<div class="handoff-ready">⛑ Human dispatcher handoff — intake complete · ${ChronosUI.esc((inc && inc.escalation_reason) || "high-risk case")}</div>`
    );
  } else if (inc && inc.escalation_required && !snap.intake_complete) {
    insertAfter.insertAdjacentHTML(
      "afterend",
      `<div class="handoff-pending">📋 Gathering required info — ${(inc.missing_slots || []).length} item(s) remaining</div>`
    );
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
  const alert = $("dispatch-alert");
  const body = $("dispatch-alert-body");
  const disp = snap.dispatches || [];

  if (!alert || !body) return;

  if (!isLive || !disp.length) {
    alert.style.display = "none";
    body.innerHTML = "";
    return;
  }

  alert.style.display = "block";
  body.innerHTML = ChronosUI.renderDispatchAlertHtml(snap);
}

function renderChecklist(snap, isLive, events) {
  const el = $("checklist");
  if (!isLive) {
    el.innerHTML = '<div class="empty">Start a call to begin SOP intake.</div>';
    return;
  }
  snap._events = events || [];
  el.innerHTML = ChronosUI.renderSopIntakeTable(snap, snap.recommended_slot);
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
    renderSopContext(snap.incident, snap, isLive);
    renderEscalation(snap, isLive);
    renderChecklist(snap, isLive, events);
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
