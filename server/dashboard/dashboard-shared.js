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
    callback_number: ["contact:callback_number", "contact:callback_phone", "contact:phone", "contact:phone_number"],
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

  function extractPhoneFromText(text) {
    if (!text) return null;
    const chunkRe = /(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}|\d{10,}/g;
    const chunks = String(text).match(chunkRe) || [];
    for (let i = chunks.length - 1; i >= 0; i--) {
      const digits = chunks[i].replace(/\D/g, "");
      if (digits.length >= 10) {
        const d =
          digits.length >= 11 && digits[0] === "1" ? digits.slice(-10) : digits.slice(-10);
        if (d.length === 10) return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
        return digits;
      }
    }
    const compact = String(text).replace(/\s/g, "");
    const m = compact.match(/\d{10,}/);
    return m ? m[0] : null;
  }

  function phoneFromSnapshot(snap) {
    const texts = [];
    for (const t of snap.turns || []) texts.push(t);
    for (const e of snap._events || []) {
      if (e.event_type === "final_transcript" && e.data && e.data.text) texts.push(e.data.text);
    }
    for (let i = texts.length - 1; i >= 0; i--) {
      const p = extractPhoneFromText(texts[i]);
      if (p) return p;
    }
    return extractPhoneFromText(texts.join(" "));
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
    if (slot === "callback_number") {
      return phoneFromSnapshot(snap);
    }
    if (slot === "breathing" && (inc.hazards || []).includes("breathing")) {
      return "Breathing difficulty reported";
    }
    if (slot === "consciousness" && inc.caller_safety === "at_risk") {
      return "Caller reports symptoms / at risk";
    }
    return null;
  }

  const GENERIC_SLOT_VALUES =
    /^(weapon mentioned|injuries reported|injury reported|breathing difficulty|confirmed|provided|yes|unknown|break-in \/ intruder at door)$/i;

  function isGenericSlotValue(val) {
    if (!val) return true;
    const s = String(val).trim();
    if (!s || GENERIC_SLOT_VALUES.test(s)) return true;
    const low = s.toLowerCase();
    return low.endsWith(" mentioned") || low.endsWith(" reported");
  }

  function intakeKnownValue(item, snap, noteMap) {
    if (!item.resolved) return null;
    const displays = snap.slot_display_values || {};
    if (displays[item.slot] && !isGenericSlotValue(displays[item.slot])) return displays[item.slot];
    const fromNotes = lookupNoteValue(SLOT_NOTE_KEYS[item.slot] || [], noteMap);
    if (fromNotes && !isGenericSlotValue(fromNotes)) return fromNotes;
    const fromState = slotValueFromState(item.slot, snap);
    if (fromState && !isGenericSlotValue(fromState)) return fromState;
    return null;
  }

  function slotKnownValue(slot, snap, noteMap) {
    const displays = snap.slot_display_values || {};
    if (displays[slot] && !isGenericSlotValue(displays[slot])) return displays[slot];
    const fromNotes = lookupNoteValue(SLOT_NOTE_KEYS[slot] || [], noteMap);
    if (fromNotes && !isGenericSlotValue(fromNotes)) return fromNotes;
    const fromState = slotValueFromState(slot, snap);
    if (fromState && !isGenericSlotValue(fromState)) return fromState;
    return null;
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
    for (const item of (checklist || []).filter((c) => c.active && c.resolved)) {
      const val = intakeKnownValue(item, snap, noteMap);
      if (val) {
        for (const k of noteKeysForSlot(item.slot)) used.add(k);
      }
    }
    return used;
  }

  function resolveChecklistItems(snap) {
    const items = (snap.checklist || []).filter((c) => c.active);
    if (items.length) return items;
    const plan = snap.sop_plan;
    const inc = snap.incident || {};
    if (!plan || !Array.isArray(plan.slots) || !inc.incident_type) return [];
    const resolved = new Set(inc.resolved_slots || []);
    return plan.slots.map((s) => ({
      slot: s.id,
      label: s.label || String(s.id).replace(/_/g, " "),
      question: s.question,
      priority: s.priority || 99,
      category: s.category || "general",
      resolved: resolved.has(s.id),
      active: true,
    }));
  }

  function renderSopIntakeTable(snap, recommendedSlot) {
    const checklist = resolveChecklistItems(snap);
    if (!checklist.length) {
      if (snap.incident && snap.incident.incident_type) {
        return '<div class="empty">Building SOP checklist for this protocol…</div>';
      }
      return '<div class="empty">Waiting for incident classification…</div>';
    }

    const noteMap = buildNoteMap(snap.structured_notes);
    const sorted = [...checklist].sort((a, b) => {
      const ai = CHECKLIST_ORDER.indexOf(a.category || "general");
      const bi = CHECKLIST_ORDER.indexOf(b.category || "general");
      if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
      return (a.priority || 99) - (b.priority || 99);
    });

    const rows = sorted
      .map((c) => {
        const isRec = c.slot === recommendedSlot;
        const known = intakeKnownValue(c, snap, noteMap);
        const status = c.resolved ? "Done" : isRec ? "Next" : "Open";
        const rowCls = ["sop-row", c.resolved ? "done" : "", isRec ? "rec" : ""].filter(Boolean).join(" ");
        const mark = c.resolved ? "✓" : isRec ? "▶" : "○";
        let valueCell;
        if (known && !isGenericSlotValue(known)) {
          valueCell = `<span class="intake-val" title="Latest captured value">${esc(known)}</span>`;
        } else {
          valueCell = `<span class="intake-q">${esc(c.question)}</span>`;
        }
        return `<tr class="${rowCls}"><td class="sop-status">${mark} ${status}</td><td class="sop-label">${esc(c.label || c.slot)}</td><td class="intake-cell">${valueCell}</td></tr>`;
      })
      .join("");

    return `<div class="sop-table-wrap"><table class="sop-table intake-table"><thead><tr><th>Status</th><th>Data point</th><th>Known / ask</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderMergedIntakeHtml(snap, recommendedSlot) {
    const checklist = resolveChecklistItems(snap);
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
        const isRec = c.slot === recommendedSlot;
        const known = intakeKnownValue(c, snap, noteMap);
        const status = c.resolved ? "Done" : isRec ? "Next" : "Open";
        const rowCls = ["sop-row", c.resolved ? "done" : "", isRec ? "rec" : ""].filter(Boolean).join(" ");
        const mark = c.resolved ? "✓" : isRec ? "▶" : "○";
        const valueCell =
          known && !isGenericSlotValue(known)
            ? `<span class="intake-val" title="Latest captured value">${esc(known)}</span>`
            : `<span class="intake-q">${esc(c.question)}</span>`;
        return `<tr class="${rowCls}"><td class="sop-status">${mark} ${status}</td><td class="sop-label">${esc(c.label || c.slot)}</td><td class="intake-cell">${valueCell}</td></tr>`;
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
      rows += `<tr class="sop-row extra"><td class="sop-status">✓ Fact</td><td class="sop-label">${esc(n.field.replace(/_/g, " "))}</td><td class="intake-cell"><span class="intake-val">${esc(n.value)}</span></td></tr>`;
    }

    if (!rows) return '<div class="empty">Waiting for incident classification…</div>';

    return `<div class="sop-table-wrap"><table class="sop-table intake-table"><thead><tr><th>Status</th><th>Data point</th><th>Known / ask</th></tr></thead><tbody>${rows}</tbody></table></div>`;
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
    const html = renderDispatchAlertHtml(snap);
    if (html) return html;
    return '<div class="empty">Units dispatch when location + incident type are known.</div>';
  }

  const DISPATCH_UNIT_META = {
    fire: { icon: "🚒", headline: "Fire department dispatched", short: "FIRE" },
    police: { icon: "🚔", headline: "Police dispatched", short: "POLICE" },
    ems: { icon: "🚑", headline: "Ambulance / EMS dispatched", short: "EMS" },
  };

  function renderDispatchAlertHtml(snap) {
    const dispatches = snap.dispatches || [];
    if (!dispatches.length) return "";
    return dispatches
      .map((d) => {
        const meta = DISPATCH_UNIT_META[d.unit_type] || {
          icon: "📡",
          headline: "Unit dispatched",
          short: (d.unit_type || "unit").toUpperCase(),
        };
        return `<div class="dispatch-alert-unit dispatch-alert-${esc(d.unit_type || "unit")}">
      <div class="da-icon" aria-hidden="true">${meta.icon}</div>
      <div class="da-copy">
        <div class="da-headline">${esc(meta.headline)}</div>
        <div class="da-status">● SIMULATED · EN ROUTE</div>
        <div class="da-loc">${esc(d.location || "—")}</div>
        ${d.reason ? `<div class="da-reason">${esc(d.reason)}</div>` : ""}
      </div>
    </div>`;
      })
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

  function checklistProgress(checklistOrSnap) {
    const items = Array.isArray(checklistOrSnap)
      ? (checklistOrSnap || []).filter((c) => c.active)
      : resolveChecklistItems(checklistOrSnap || {});
    const done = items.filter((c) => c.resolved).length;
    return { done, total: items.length, pct: items.length ? Math.round((done / items.length) * 100) : 0 };
  }

  function transcriptMessageCount(snap, events) {
    let n = 0;
    for (const ev of events || []) {
      if (
        ev.event_type === "final_transcript" ||
        ev.event_type === "agent_guidance" ||
        ev.event_type === "background_speech"
      ) {
        n++;
      }
    }
    if (!n && snap.turns) n = snap.turns.length;
    return n;
  }

  function bindTranscriptScroll(el) {
    if (!el || el.dataset.scrollBound === "1") return;
    el.dataset.scrollBound = "1";
    el.dataset.scrollPinned = "true";
    el.addEventListener("scroll", () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      el.dataset.scrollPinned = dist < 64 ? "true" : "false";
    });
  }

  function maybeAutoScrollTranscript(el, messageCount) {
    if (!el) return;
    bindTranscriptScroll(el);
    const prev = parseInt(el.dataset.msgCount || "0", 10);
    el.dataset.msgCount = String(messageCount);
    if (messageCount <= prev) return;
    if (el.dataset.scrollPinned === "false") return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }

  return {
    esc,
    incidentLabel,
    protocolTitle,
    riskClass,
    renderTranscriptHtml,
    transcriptMessageCount,
    bindTranscriptScroll,
    maybeAutoScrollTranscript,
    renderChecklistGrouped,
    renderChecklistTable,
    renderChecklistFlat,
    renderMemoryHtml,
    renderStructuredNotesHtml,
    renderMergedIntakeHtml,
    renderSopIntakeTable,
    resolveChecklistItems,
    renderDispatchesHtml,
    renderDispatchAlertHtml,
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
