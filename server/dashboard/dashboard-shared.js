"use strict";

/** Shared Chronos dashboard rendering helpers (live + full dashboard). */
const ChronosUI = (() => {
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const INCIDENT_LABELS = {
    structure_fire: "Structure Fire",
    vehicle_crash: "Vehicle Crash",
    medical: "Medical Emergency",
    non_emergency_noise: "Noise Complaint",
    possible_active_disturbance: "Active Disturbance",
    active_threat: "Active Threat",
  };

  const PROTOCOL_TITLES = {
    structure_fire: "Fire & Smoke Protocol",
    vehicle_crash: "Vehicle Crash Protocol",
    medical: "Medical Triage Protocol",
    non_emergency_noise: "Non-Emergency Intake",
    possible_active_disturbance: "Disturbance Escalation",
    active_threat: "Threat Response Protocol",
  };

  const CATEGORY_META = {
    location: { label: "Location", icon: "📍" },
    safety: { label: "Caller Safety", icon: "🛡" },
    third_party: { label: "Third-Party Risk", icon: "👥" },
    medical: { label: "Medical Status", icon: "🩺" },
    vehicle: { label: "Vehicle / Road", icon: "🚗" },
    hazard: { label: "Hazards", icon: "⚠" },
    contact: { label: "Contact", icon: "📞" },
    general: { label: "General", icon: "☑" },
  };

  const MEMORY_ICONS = {
    prior_call: "🕐",
    sop: "📋",
    eval_failure: "⚡",
    location_alias: "📍",
    failure_memory: "⚡",
  };

  function incidentLabel(type) {
    return INCIDENT_LABELS[type] || (type ? type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "Unknown");
  }

  function protocolTitle(type) {
    return PROTOCOL_TITLES[type] || "Emergency Intake Protocol";
  }

  function riskClass(r) {
    return "risk-" + (r || "unknown");
  }

  function renderTranscriptHtml(snap, events, large) {
    const items = [];
    for (const ev of events) {
      if (ev.event_type === "final_transcript") items.push({ kind: "caller", text: ev.data.text });
      else if (ev.event_type === "background_speech") items.push({ kind: "background", text: ev.data.text });
      else if (ev.event_type === "agent_guidance") items.push({ kind: "agent", text: ev.data.text });
    }
    if (items.length === 0 && snap.turns) {
      for (const t of snap.turns) items.push({ kind: "caller", text: t });
    }
    const who = { caller: "Caller", background: "Background", agent: "Chronos" };
    return items
      .map(
        (i) =>
          `<div class="turn ${i.kind}">${large ? `<span class="who">${who[i.kind]}</span>` : ""}${esc(i.text)}</div>`
      )
      .join("");
  }

  function renderChecklistTable(checklist, recommendedSlot) {
    const items = (checklist || []).filter((c) => c.active);
    if (!items.length) return "";

    const order = ["location", "safety", "third_party", "medical", "vehicle", "hazard", "contact", "general"];
    items.sort((a, b) => {
      const ai = order.indexOf(a.category || "general");
      const bi = order.indexOf(b.category || "general");
      if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
      return (a.priority || 99) - (b.priority || 99);
    });

    let rows = items
      .map((c) => {
        const meta = CATEGORY_META[c.category] || CATEGORY_META.general;
        const isRec = c.slot === recommendedSlot;
        const status = c.resolved ? "Done" : isRec ? "Next" : "Open";
        const rowCls = ["sop-row", c.resolved ? "done" : "", isRec ? "rec" : ""].filter(Boolean).join(" ");
        const mark = c.resolved ? "✓" : isRec ? "▶" : "○";
        return `<tr class="${rowCls}"><td class="sop-status">${mark} ${status}</td><td class="sop-cat">${meta.icon} ${esc(meta.label)}</td><td class="sop-label">${esc(c.label || c.slot)}</td><td class="sop-q">${esc(c.question)}</td></tr>`;
      })
      .join("");

    return `<div class="sop-table-wrap"><table class="sop-table"><thead><tr><th>Status</th><th>Category</th><th>Data point</th><th>Question to ask</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderChecklistGrouped(checklist, recommendedSlot) {
    const items = (checklist || []).filter((c) => c.active);
    if (!items.length) return "";

    const byCat = {};
    for (const c of items) {
      const cat = c.category || "general";
      (byCat[cat] = byCat[cat] || []).push(c);
    }

    const order = ["location", "safety", "third_party", "medical", "vehicle", "hazard", "contact", "general"];
    const cats = [...order.filter((k) => byCat[k]), ...Object.keys(byCat).filter((k) => !order.includes(k))];

    let html = "";
    for (const cat of cats) {
      const meta = CATEGORY_META[cat] || CATEGORY_META.general;
      html += `<div class="checklist-group"><div class="checklist-group-head"><span class="ico">${meta.icon}</span>${esc(meta.label)}</div>`;
      for (const c of byCat[cat]) {
        const isRec = c.slot === recommendedSlot;
        const cls = ["sop-item", c.resolved ? "done" : "", isRec ? "rec" : ""].filter(Boolean).join(" ");
        const mark = c.resolved ? "✓" : isRec ? "▶" : "○";
        html += `<div class="${cls}"><span class="mark">${mark}</span><div class="body"><div class="q">${esc(c.question)}</div><div class="slot-id">${esc(c.label || c.slot)}</div></div></div>`;
      }
      html += "</div>";
    }
    return html;
  }

  function renderChecklistFlat(checklist, recommendedSlot) {
    const items = (checklist || []).filter((c) => c.active);
    return items
      .map((c) => {
        const cls = [c.resolved ? "resolved" : "", c.slot === recommendedSlot ? "rec" : ""].join(" ");
        const mark = c.resolved ? "✓" : c.slot === recommendedSlot ? "▶" : "○";
        const cat = CATEGORY_META[c.category] || CATEGORY_META.general;
        return `<li class="${cls}"><span class="mark">${mark}</span><span class="q">${esc(c.question)}<div class="slot">${cat.icon} ${esc(c.label || c.slot)}</div></span></li>`;
      })
      .join("");
  }

  function renderStructuredNotesHtml(snap) {
    const notes = snap.structured_notes || [];
    if (!notes.length) return '<div class="empty">Facts will appear here as the caller speaks.</div>';
    const rows = notes
      .map(
        (n) =>
          `<tr><td class="note-cat">${esc(n.category)}</td><td class="note-field">${esc(n.field.replace(/_/g, " "))}</td><td class="note-val">${esc(n.value)}</td></tr>`
      )
      .join("");
    return `<div class="notes-table-wrap"><table class="notes-table"><thead><tr><th>Category</th><th>Field</th><th>Value</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderDispatchesHtml(snap) {
    const dispatches = snap.dispatches || [];
    if (!dispatches.length) return '<div class="empty">Units dispatch when location + incident type are known.</div>';
    const icons = { fire: "🚒", police: "🚔", ems: "🚑" };
    return dispatches
      .map(
        (d) =>
          `<div class="dispatch-chip"><span class="d-ico">${icons[d.unit_type] || "📡"}</span><span class="d-type">${esc((d.unit_type || "").toUpperCase())}</span><span class="d-loc">${esc(d.location || "—")}</span><span class="d-reason">${esc(d.reason || "")}</span></div>`
      )
      .join("");
  }

  function renderMemoryHtml(snap) {
    const results = (snap.memory && snap.memory.results) || [];
    if (!results.length) return '<div class="empty">No memory retrieved yet.</div>';
    return results
      .slice(0, 6)
      .map((m) => {
        const icon = MEMORY_ICONS[m.memory_type] || "💾";
        return `<div class="mem ${esc(m.memory_type)}"><span class="type">${icon} ${esc(m.memory_type.replace(/_/g, " "))}</span>${m.score != null ? `<span class="score">${Number(m.score).toFixed(2)}</span>` : ""}<div class="content">${esc(m.content)}</div></div>`;
      })
      .join("");
  }

  function renderIncidentHtml(inc) {
    if (!inc || !inc.incident_type) {
      return '<div class="k">incident</div><div class="v">listening…</div>';
    }
    const tp = inc.third_party_risk;
    const tpClass = tp === "active" ? "tp-active" : tp === "resolved" ? "tp-resolved" : "";
    const hazards = (inc.hazards || []).map((h) => `<span class="hazard">${esc(h)}</span>`).join("") || "—";
    let html = "";
    const row = (k, v) => (html += `<div class="k">${k}</div><div class="v">${v}</div>`);
    const plan = inc._planDisplay || "";
    row("protocol", esc(plan || protocolTitle(inc.incident_type)));
    row("type", esc(incidentLabel(inc.incident_type)) + (inc.upgraded_to ? ` → ${esc(inc.upgraded_to)}` : ""));
    row("risk", `<span class="badge ${riskClass(inc.risk_level)}">${esc(inc.risk_level)}</span>`);
    row(
      "location",
      esc(inc.location_raw || "unknown") +
        (inc.location_needs_confirmation && inc.location_raw ? ' <span class="badge risk-medium">confirm</span>' : "")
    );
    row("caller safety", esc(inc.caller_safety));
    row("third-party risk", `<span class="${tpClass}">${esc(tp)}</span>`);
    row("hazards", hazards);
    return html;
  }

  function checklistProgress(checklist) {
    const items = (checklist || []).filter((c) => c.active);
    const done = items.filter((c) => c.resolved).length;
    return { done, total: items.length, pct: items.length ? Math.round((done / items.length) * 100) : 0 };
  }

  return {
    esc,
    incidentLabel,
    protocolTitle,
    riskClass,
    renderTranscriptHtml,
    renderChecklistGrouped,
    renderChecklistTable,
    renderChecklistFlat,
    renderMemoryHtml,
    renderStructuredNotesHtml,
    renderDispatchesHtml,
    renderIncidentHtml,
    checklistProgress,
    CATEGORY_META,
  };
})();

// Global esc for inline scripts
function esc(s) {
  return ChronosUI.esc(s);
}
