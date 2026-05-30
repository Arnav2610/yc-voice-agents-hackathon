# Chronos 911 — Presenter Demo Script (~90 seconds)

> Opening line: *"This is a simulated 911 training copilot. It does not replace dispatchers
> and never dispatches anyone."*

## One-time prep (before you present)

```bash
cd server
uv sync
make reset          # baseline (un-patched) policies
make seed           # seed Supermemory + local memory
make regression     # writes baseline metrics for the dashboard
```

Open two browser tabs: **http://localhost:7860** (call) and **http://localhost:7861** (dashboard).

---

## The pitch (15s)

> "Everyone's building self-improving voice agents. Most improve by patching the **prompt**.
> Chronos improves like an emergency operations team — it remembers prior incidents, SOPs, and
> past eval failures, and it patches what it **retrieves, escalates, and leaves unresolved**.
> Production voice agents shouldn't just learn what to say. They should learn what not to forget."

## Beat 1 — Baseline failure (15s)

```bash
make regression
```

> "A Cekura-style regression over 12 emergency scenarios. Baseline passes about **two-thirds**.
> It's failing a specific, dangerous class: it closes the trapped-person safety branch when the
> **caller** gets out — even if a neighbor or child might still be inside."

Point at: `wrong_branch_closure: 4`, `missed_trapped_person_question: 3`.

## Beat 2 — Live call (35s)

Start the bot (`make bot`), open :7860, click **Connect**, and read the caller script aloud:

> *"There's smoke in my apartment building. I'm on the third floor. I think my neighbor is
> still inside. I don't know the exact address — I'm near 5th and Pine. I called yesterday
> about a gas smell but no one came."*

Watch the **dashboard** (:7861) and narrate:
- **Incident state:** `structure_fire`, risk **critical**, third-party risk **active**.
- **Memory retrieval:** the **prior gas-smell call near 5th and Pine** surfaces (real
  Supermemory), plus the structure-fire SOP and the prior eval-failure memory.
- **SOP checklist:** required questions light up; the recommended next question is highlighted.
- **Escalation:** "Recommend human dispatcher" banner.
- The bot **speaks** the next safety question and never tells the caller to re-enter.

> (No mic? `make democall` plays this exact scenario onto the dashboard.)

## Beat 3 — Self-improvement (20s)

```bash
make improve
```

> "Chronos classifies the failure as **WRONG_BRANCH_CLOSURE**, writes the lesson to Supermemory,
> and generates a **safe, structured** policy patch — not arbitrary code. It adds a guard so
> caller evacuation can no longer close the third-party branch, reruns the regression, and only
> keeps the patch because it improves with **no new critical regression**."

Point at the dashboard **Self-improvement** panel and **Policy patch** diff:

| Metric | Before | After |
|---|---|---|
| Pass rate | 66.7% | **100%** |
| Wrong branch closure | 4 | **0** |
| Missed trapped-person Q | 3 | **0** |

```diff
+  cannot_be_resolved_by:
+    - caller_personally_evacuated
+    - caller_says_i_am_safe
+  required_until_resolved:
+    - ask_if_anyone_inside
+    - ask_last_known_location
+    - escalate_human
```

## Close (5s)

> "Real before/after, computed by replaying the scenarios against the patched policy — and the
> learned rule is now in Supermemory, so future calls retrieve it. That's an agent that improves
> like an operations team."

---

## Other scenarios to show (optional)

| Command | Shows |
|---|---|
| `make democall SCENARIO=vehicle_crash_location_correction_001` | Self-correction → suppressed interruption, location marked uncertain |
| `make democall SCENARIO=noise_escalation_violence_001` | Non-emergency noise **upgrades** to active disturbance |
| `make democall SCENARIO=background_safety_critical_001` | A **background** speaker ("the baby is still inside") is treated as safety-critical |
| `make democall SCENARIO=medical_chest_pain_001` | Safe-scope medical: escalate, no diagnosis |

## Reset between runs

```bash
make reset
```

## Safety reminders (say at least once)

- Simulated training system; not a real 911 service; no responders dispatched.
- Always recommends a **human dispatcher** for real emergencies.
