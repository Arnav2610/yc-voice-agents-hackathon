"""Chronos dashboard server (FastAPI).

Runs in-process with the bot (daemon thread) so it shares the live EventStore and
the active kernel snapshot. Also serves the static dashboard UI from dashboard/.
Eval/improvement scripts write JSON into chronos/runtime/, which the metrics and
policy-diff endpoints read — so the dashboard works whether or not a call is live.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from chronos import config
from chronos.events import STORE

# Registry for the currently active live call kernel (set by the bot).
LIVE: dict[str, Any] = {"kernel": None}

# Last demo action's status, polled by the dashboard control bar.
JOB: dict[str, Any] = {"action": None, "status": "idle", "message": "", "ts": 0}


def set_live_kernel(kernel) -> None:
    LIVE["kernel"] = kernel


def _set_job(action: str, status: str, message: str = "") -> None:
    JOB.update({"action": action, "status": status, "message": message, "ts": config.now_ms()})


def _read_runtime(name: str) -> Any:
    path = config.RUNTIME_DIR / name
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def create_app() -> FastAPI:
    app = FastAPI(title="Chronos 911 Dashboard", docs_url="/api-docs")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/chronos/maps-config")
    def maps_config() -> dict[str, Any]:
        """Public Maps Embed key for the local dashboard (restrict key by HTTP referrer in GCP)."""
        key = os.getenv("MAPS_API_KEY", "").strip()
        return {"enabled": bool(key), "embedKey": key or None}

    @app.get("/chronos/health")
    def health() -> dict[str, Any]:
        k = LIVE["kernel"]
        return {
            "ok": True,
            "mode": config.CHRONOS_MODE,
            "disclaimer": config.SIMULATION_DISCLAIMER,
            "live_call": k.state.call_id if k else None,
            "calls": STORE.call_ids(),
        }

    @app.get("/chronos/state")
    def state() -> dict[str, Any]:
        k = LIVE["kernel"]
        if k:
            return k.state.snapshot()
        return _read_runtime("latest.json") or {}

    @app.get("/chronos/latest")
    def latest() -> dict[str, Any]:
        # In-process live kernel wins; otherwise fall back to the disk-mirrored
        # live state written by the bot process (cross-process live view).
        k = LIVE["kernel"]
        if k:
            payload = {
                "snapshot": k.state.snapshot(),
                "events": STORE.list_latest(),
                "disclaimer": config.SIMULATION_DISCLAIMER,
                "source": "in_process",
            }
        else:
            live = _read_runtime("live.json")
            snap = (live or {}).get("snapshot") or {}
            has_live = bool(live) and (live.get("events") or snap.get("call_id"))
            if has_live:
                payload = {**live, "source": "live_json"}
            else:
                payload = {
                    "snapshot": _read_runtime("latest.json") or {},
                    "events": [],
                    "disclaimer": config.SIMULATION_DISCLAIMER,
                    "source": "latest_json",
                }
        return JSONResponse(payload, headers={"Cache-Control": "no-store, max-age=0"})

    @app.get("/chronos/events")
    def events_latest() -> list[dict[str, Any]]:
        evs = STORE.list_latest()
        if evs:
            return evs
        return (_read_runtime("live.json") or {}).get("events", [])

    @app.get("/chronos/events/{call_id}")
    def events(call_id: str) -> list[dict[str, Any]]:
        return STORE.list(call_id)

    @app.get("/chronos/improvement")
    def improvement() -> dict[str, Any]:
        return _read_runtime("improvement.json") or {}

    @app.get("/chronos/metrics")
    def metrics() -> dict[str, Any]:
        imp = _read_runtime("improvement.json")
        if imp:
            return {
                "status": imp.get("status"),
                "before": imp.get("before"),
                "after": imp.get("after"),
                "failure": imp.get("failure"),
            }
        return {"status": "no_run", "before": _read_runtime("metrics_before.json") or {}, "after": {}}

    @app.get("/chronos/policy-diff")
    def policy_diff() -> dict[str, Any]:
        imp = _read_runtime("improvement.json")
        return {
            "diff": imp.get("policy_diff", ""),
            "patch": imp.get("patch", {}),
            "status": imp.get("status"),
        }

    @app.get("/chronos/policy")
    def policy() -> dict[str, Any]:
        return {name: pol for name, pol in config.load_all_policies().items()}

    @app.get("/chronos/scenarios")
    def scenarios() -> list[dict[str, Any]]:
        from chronos.improvement_loop import load_scenarios

        return [
            {"id": s["id"], "title": s.get("title", s["id"]), "family": s.get("incident_family", "")}
            for s in load_scenarios()
        ]

    @app.get("/chronos/cekura")
    def cekura() -> dict[str, Any]:
        return _read_runtime("cekura_live_result.json") or {}

    @app.get("/chronos/job")
    def job() -> dict[str, Any]:
        return JOB

    # --- demo control actions (drive the whole flow from the browser) -------
    @app.post("/chronos/actions/seed")
    async def action_seed() -> dict[str, Any]:
        _set_job("seed", "running", "Seeding institutional memory…")

        async def _run():
            try:
                from chronos.memory_retrieval import ChronosMemoryClient

                res = await asyncio.to_thread(
                    lambda: ChronosMemoryClient(api_key=os.getenv("SUPERMEMORY_API_KEY")).seed()
                )
                _set_job("seed", "done", f"Seeded ({res['mode']}): {res['local_records']} local, {res['pushed_to_supermemory']} to Supermemory")
            except Exception as e:  # noqa: BLE001
                _set_job("seed", "error", str(e))

        asyncio.create_task(_run())
        return {"status": "started"}

    @app.post("/chronos/actions/play")
    async def action_play(body: dict[str, Any]) -> dict[str, Any]:
        scenario_id = (body or {}).get("scenario_id", "structure_fire_prior_gas_001")
        _set_job("play", "running", f"Playing scenario {scenario_id}…")

        async def _run():
            try:
                import uuid

                from chronos.improvement_loop import get_scenario
                from chronos.kernel import ChronosKernel
                from chronos.memory_retrieval import ChronosMemoryClient

                scenario = get_scenario(scenario_id)
                if not scenario:
                    _set_job("play", "error", f"Unknown scenario {scenario_id}")
                    return
                mem = ChronosMemoryClient(api_key=os.getenv("SUPERMEMORY_API_KEY"))
                # Unique call_id per playthrough so the trace is fresh each time.
                call_id = f"demo_{scenario_id}_{uuid.uuid4().hex[:6]}"
                kernel = ChronosKernel(call_id, scenario_id=scenario_id, memory_client=mem, event_store=STORE)
                set_live_kernel(kernel)
                for turn in scenario.get("turns", []):
                    if turn.get("speaker") == "background":
                        await kernel.process_background_speech(turn.get("text", ""))
                    else:
                        await kernel.process_caller_turn(turn.get("text", ""))
                    await asyncio.sleep(1.2)  # let the panels stream visibly
                await kernel.on_call_complete()
                _set_job("play", "done", f"Played {scenario_id}")
            except Exception as e:  # noqa: BLE001
                _set_job("play", "error", str(e))

        asyncio.create_task(_run())
        return {"status": "started"}

    @app.post("/chronos/actions/regression")
    async def action_regression() -> dict[str, Any]:
        _set_job("regression", "running", "Running baseline regression…")

        async def _run():
            try:
                import json

                from chronos.improvement_loop import run_suite

                suite = await run_suite(event_store=STORE)
                with open(config.RUNTIME_DIR / "metrics_before.json", "w") as f:
                    json.dump(suite.summary, f, indent=2)
                s = suite.summary
                _set_job("regression", "done", f"Baseline: {int(s['pass_rate']*100)}% pass, wrong_branch={s['wrong_branch_closure']}, missed_trapped={s['missed_trapped_person_question']}")
            except Exception as e:  # noqa: BLE001
                _set_job("regression", "error", str(e))

        asyncio.create_task(_run())
        return {"status": "started"}

    @app.post("/chronos/actions/improve")
    async def action_improve() -> dict[str, Any]:
        _set_job("improve", "running", "Classifying failure, patching policy, rerunning regression…")

        async def _run():
            try:
                from chronos.improvement_loop import run_improvement

                rep = await run_improvement(event_store=STORE)
                if rep.get("status") == "accepted":
                    b, a = rep["before"], rep["after"]
                    _set_job("improve", "done", f"Patch ACCEPTED: {int(b['pass_rate']*100)}% → {int(a['pass_rate']*100)}% (wrong_branch {b['wrong_branch_closure']}→{a['wrong_branch_closure']})")
                else:
                    _set_job("improve", "done", f"Status: {rep.get('status')}")
            except Exception as e:  # noqa: BLE001
                _set_job("improve", "error", str(e))

        asyncio.create_task(_run())
        return {"status": "started"}

    @app.post("/chronos/actions/reset")
    async def action_reset() -> dict[str, Any]:
        try:
            from chronos.improvement_loop import revert_policies_to_baseline

            revert_policies_to_baseline()
            for name in ("improvement.json", "latest.json", "live.json"):
                p = config.RUNTIME_DIR / name
                if p.exists():
                    p.unlink()
            LIVE["kernel"] = None
            STORE.clear()  # blank the live transcript/trace for a clean re-demo
            _set_job("reset", "done", "Reverted policies to baseline; cleared run reports and live trace.")
            return {"status": "done"}
        except Exception as e:  # noqa: BLE001
            _set_job("reset", "error", str(e))
            return {"status": "error", "message": str(e)}

    # --- static UI ----------------------------------------------------------
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(config.DASHBOARD_DIR / "index.html"))

    @app.get("/live")
    def live() -> FileResponse:
        """Minimal, glanceable view for a live (mic) demo."""
        return FileResponse(str(config.DASHBOARD_DIR / "live.html"))

    @app.get("/dashboard-shared.js")
    def dashboard_shared() -> FileResponse:
        return FileResponse(str(config.DASHBOARD_DIR / "dashboard-shared.js"), media_type="application/javascript")

    @app.get("/app.js")
    def appjs() -> FileResponse:
        return FileResponse(str(config.DASHBOARD_DIR / "app.js"), media_type="application/javascript")

    @app.get("/live-app.js")
    def live_appjs() -> FileResponse:
        return FileResponse(str(config.DASHBOARD_DIR / "live-app.js"), media_type="application/javascript")

    @app.get("/styles.css")
    def styles() -> FileResponse:
        return FileResponse(str(config.DASHBOARD_DIR / "styles.css"), media_type="text/css")

    @app.get("/favicon.ico")
    def favicon() -> JSONResponse:
        return JSONResponse({}, status_code=204)

    return app


app = create_app()


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _peer_dashboard_ok(port: int) -> bool:
    """True if something on `port` looks like a current Chronos dashboard."""
    import urllib.error
    import urllib.request

    def _ok(path: str) -> bool:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    return _ok("/chronos/health") and _ok("/dashboard-shared.js")


def start_dashboard_in_thread(port: int | None = None) -> threading.Thread | None:
    """Start uvicorn for the dashboard in a daemon thread (shares this process'
    memory with the bot).

    If the port is already taken (e.g. a separately-launched `make dash`), DON'T
    start a second server — log loudly and rely on the bot's disk-mirrored
    runtime/live.json, which the existing dashboard reads as a fallback. This
    avoids the silent bind-failure where a stale dashboard ends up serving an
    empty view of the live call.
    """
    import uvicorn
    from loguru import logger

    port = port or config.DASHBOARD_PORT
    if _port_in_use(port):
        if _peer_dashboard_ok(port):
            logger.info(
                f"Chronos dashboard already on http://localhost:{port} — bot will mirror live state "
                f"to runtime/live.json (open /live to watch)."
            )
        else:
            logger.error(
                f"Port {port} is in use by a STALE dashboard (missing /dashboard-shared.js or /chronos API). "
                f"The live view will NOT work until you restart it:\n"
                f"  pkill -f 'chronos.dashboard_server' ; cd server && make dash\n"
                f"Or stop that process and restart `make bot` so the bot hosts the dashboard itself."
            )
        return None
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True, name="chronos-dashboard")
    t.start()
    logger.info(f"Chronos dashboard on http://localhost:{port}  (live view: /live)")
    return t


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv(override=True)  # so browser-driven actions use real Supermemory/Nemotron
    uvicorn.run(app, host="0.0.0.0", port=config.DASHBOARD_PORT, log_level="info")
