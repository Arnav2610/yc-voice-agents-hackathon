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
    incident: { label: "Incident", icon: "🚨" },
    threat: { label: "Threat", icon: "⚠" },
    victim: { label: "Victims", icon: "🧑" },
    general: { label: "General", icon: "☑" },
  };

  const NOTE_FIELD_ALIASES = {
    "location:address": ["address", "address_or_landmark", "exact_location", "landmark", "location"],
    "contact:callback_number": ["callback_number", "callback_phone", "phone", "phone_number"],
    "contact:caller_name": ["caller_name", "name", "callback_name"],
    "threat:weapon_type": ["weapon_type", "weapon", "armed"],
    "threat:threat_type": ["threat_type", "description", "threat_description", "incident_description"],
    "threat:suspect_location": ["suspect_location", "intruder_location", "door_status"],
  };

  const SLOT_NOTE_KEYS = {
    exact_location: ["location:address", "location:landmark"],
    location: ["location:address", "location:landmark"],
    caller_safety: ["safety:caller_status"],
    trapped_person_status: ["third_party:persons_at_risk", "third_party:trapped_person", "victim:trapped"],
    last_known_location: ["third_party:last_known_location", "location:last_known"],
    callback_number: ["contact:callback_number", "contact:caller_name"],
    weapon_info: ["threat:weapon_type"],
    threat_description: ["threat:threat_type"],
    suspect_location: ["threat:suspect_location"],
    vehicle_hazard: ["vehicle:hazard", "vehicle:vehicle_hazard"],
    consciousness: ["medical:consciousness"],
    breathing: ["medical:breathing"],
    injury_status: ["medical:injury", "medical:injury_status"],
  };

  const CHECKLIST_ORDER = ["location", "safety", "third_party", "medical", "vehicle", "hazard", "contact", "general"];

  function canonicalNoteKey(category, field) {
    const cat = String(category || "other").toLowerCase();
    const fld = String(field || "").toLowerCase();
    for (const [key, aliases] of Object.entries(NOTE_FIELD_ALIASES)) {
      if (key.startsWith(cat + ":") && aliases.includes(fld)) return key;
    }
    return `${cat}:${fld}`;
  }

  function dedupeNotes(notes) {
    const byKey = new Map();
    const order = [];
    for (const n of notes || []) {
      const key = canonicalNoteKey(n.category, n.field);
      const prev = byKey.get(key);
      if (!prev || (n.turn || 0) >= (prev.turn || 0)) {
        if (!byKey.has(key)) order.push(key);
        const [cat, field] = key.split(":");
        byKey.set(key, { category: cat, field, value: n.value, turn: n.turn || 0 });
      }
    }
    return order.map((k) => byKey.get(k));
  }

  function buildNoteMap(notes) {
    const map = new Map();
    for (const n of dedupeNotes(notes)) {
      map.set(`${n.category}:${n.field}`, n);
    }
    return map;
  }

  function lookupNoteValue(keys, noteMap) {
    for (const key of keys) {
      const n = noteMap.get(key);
      if (n && n.value) return n.value;
    }
    return null;
  }

  function slotValueFromState(slot, snap) {
    const inc = snap.incident || {};
    if (slot === "exact_location" || slot === "location") {
      if (!inc.location_raw) return null;
      return inc.location_raw + (inc.location_needs_confirmation ? " (confirm exact address)" : "");
    }
    if (slot === "caller_safety") {
      const labels = {
        self_evacuated: "Outside / evacuated",
        resolved: "Caller reports safe",
        at_risk: "Caller still at risk",
      };
      return labels[inc.caller_safety] || null;
    }
    if (slot === "trapped_person_status" && inc.third_party_risk === "active") {
      return "Someone may still be inside / unable to exit";
    }
    if (slot === "trapped_person_status" && inc.third_party_risk === "resolved") {
      return "All persons accounted for";
    }
    return null;
  }

  function slotKnownValue(slot, snap, noteMap) {
    const fromNotes = lookupNoteValue(SLOT_NOTE_KEYS[slot] || [], noteMap);
    if (fromNotes) return fromNotes;
    return slotValueFromState(slot, snap);
  }

  function noteKeysForSlot(slot) {
    return new Set(SLOT_NOTE_KEYS[slot] || []);
  }

  function isHeaderRedundantNote(note, snap) {
    const inc = snap.incident || {};
    if (note.category === "incident" && note.field === "classification") return true;
    if (note.category === "incident" && note.field === "upgraded_to" && inc.upgraded_to) return true;
    if (note.category === "location" && note.field === "address" && inc.location_raw) {
      const norm = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
      return norm(note.value).includes(norm(inc.location_raw).slice(0, 12));
    }
    if (note.category === "safety" && note.field === "caller_status") {
      const st = slotValueFromState("caller_safety", snap);
      return st && normEq(note.value, st);
    }
    if (note.category === "third_party" && note.field === "persons_at_risk" && inc.third_party_risk !== "active") {
      return inc.third_party_risk === "resolved";
    }
    return false;
  }

  function normEq(a, b) {
    return String(a || "").toLowerCase().trim() === String(b || "").toLowerCase().trim();
  }

  function consumedNoteKeys(checklist, snap, noteMap) {
    const used = new Set();
    for (const item of (checklist || []).filter((c) => c.active)) {
      const val =
        slotKnownValue(item.slot, snap, noteMap) || lookupNoteValue(SLOT_NOTE_KEYS[item.slot] || [], noteMap);
      if (val) {
        for (const k of noteKeysForSlot(item.slot)) used.add(k);
      }
    }
    return used;
  }

  function renderMergedIntakeHtml(snap, recommendedSlot) {
    const checklist = (snap.checklist || []).filter((c) => c.active);
    const noteMap = buildNoteMap(snap.structured_notes);
    const consumed = consumedNoteKeys(checklist, snap, noteMap);

    if (!checklist.length) {
      const notes = dedupeNotes(snap.structured_notes).filter((n) => !isHeaderRedundantNote(n, snap));
      if (!notes.length) return '<div class="empty">Waiting for incident classification…</div>';
    }

    const sorted = [...checklist].sort((a, b) => {
      const ai = CHECKLIST_ORDER.indexOf(a.category || "general");
      const bi = CHECKLIST_ORDER.indexOf(b.category || "general");
      if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
      return (a.priority || 99) - (b.priority || 99);
    });

    let rows = sorted
      .map((c) => {
        const meta = CATEGORY_META[c.category] || CATEGORY_META.general;
        const isRec = c.slot === recommendedSlot;
        const known =
          slotKnownValue(c.slot, snap, noteMap) || lookupNoteValue(SLOT_NOTE_KEYS[c.slot] || [], noteMap);
        const status = c.resolved ? "Done" : isRec ? "Next" : "Open";
        const rowCls = ["sop-row", c.resolved ? "done" : "", isRec ? "rec" : ""].filter(Boolean).join(" ");
        const mark = c.resolved ? "✓" : isRec ? "▶" : "○";
        const valueCell = known
          ? `<span class="intake-val" title="Latest captured value">${esc(known)}</span>`
          : `<span class="intake-q">${esc(c.question)}</span>`;
        return `<tr class="${rowCls}"><td class="sop-status">${mark} ${status}</td><td class="sop-cat">${meta.icon} ${esc(meta.label)}</td><td class="sop-label">${esc(c.label || c.slot)}</td><td class="intake-cell">${valueCell}</td></tr>`;
      })
      .join("");

    const extraNotes = dedupeNotes(snap.structured_notes).filter((n) => {
      const key = `${n.category}:${n.field}`;
      if (consumed.has(key)) return false;
      if (isHeaderRedundantNote(n, snap)) return false;
      for (const item of checklist) {
        if (item.resolved && (SLOT_NOTE_KEYS[item.slot] || []).includes(key)) return false;
      }
      return true;
    });

    for (const n of extraNotes) {
      const meta = CATEGORY_META[n.category] || CATEGORY_META.general;
      rows += `<tr class="sop-row extra"><td class="sop-status">✓ Fact</td><td class="sop-cat">${meta.icon} ${esc(meta.label)}</td><td class="sop-label">${esc(n.field.replace(/_/g, " "))}</td><td class="intake-cell"><span class="intake-val">${esc(n.value)}</span></td></tr>`;
    }

    if (!rows) return '<div class="empty">Waiting for incident classification…</div>';

    return `<div class="sop-table-wrap"><table class="sop-table intake-table"><thead><tr><th>Status</th><th>Category</th><th>Data point</th><th>Known / ask</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

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
    return renderMergedIntakeHtml(snap, snap.recommended_slot);
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

  function renderIncidentCompactHtml(inc) {
    if (!inc || !inc.incident_type) {
      return '<span class="chip chip-listening">Listening for incident type…</span>';
    }
    const plan = inc._planDisplay || protocolTitle(inc.incident_type);
    const tp = inc.third_party_risk || "unknown";
    const tpCls = tp === "active" ? " chip-tp-active" : "";
    const loc =
      esc(inc.location_raw || "unknown") +
      (inc.location_needs_confirmation && inc.location_raw ? " · confirm" : "");
    const chips = [
      `<span class="chip chip-proto"><span class="ck">SOP</span><span class="cv">${esc(plan)}</span></span>`,
      `<span class="chip"><span class="ck">Type</span><span class="cv">${esc(incidentLabel(inc.incident_type))}${inc.upgraded_to ? ` → ${esc(inc.upgraded_to.replace(/_/g, " "))}` : ""}</span></span>`,
      `<span class="chip chip-risk"><span class="ck">Risk</span><span class="cv badge ${riskClass(inc.risk_level)}">${esc(inc.risk_level || "unknown")}</span></span>`,
      `<span class="chip"><span class="ck">Location</span><span class="cv">${loc}</span></span>`,
      `<span class="chip${tpCls}"><span class="ck">3rd party</span><span class="cv">${esc(tp)}</span></span>`,
      `<span class="chip"><span class="ck">Caller</span><span class="cv">${esc(inc.caller_safety || "unknown")}</span></span>`,
    ];
    if (inc.hazards && inc.hazards.length) {
      chips.push(
        `<span class="chip chip-tp-active"><span class="ck">Hazards</span><span class="cv">${inc.hazards.map((h) => esc(h)).join(", ")}</span></span>`
      );
    }
    return chips.join("");
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
    renderMergedIntakeHtml,
    renderDispatchesHtml,
    renderIncidentHtml,
    renderIncidentCompactHtml,
    checklistProgress,
    CATEGORY_META,
  };
})();

// Global esc for inline scripts
function esc(s) {
  return ChronosUI.esc(s);
}
