"""Supermemory-backed institutional memory, with a deterministic local fallback.

Real Supermemory is used over its REST API (v3 documents for writes, v4 search
for reads) whenever a SUPERMEMORY_API_KEY is present. A local keyword/tag store
(seeded from data/*.json + seed_sops.md) is ALWAYS available so the offline
regression simulator is deterministic and the demo never hard-fails on network.

Container-tag strategy: the agency tag (agency:demo_psap) is the Supermemory
`containerTag`; the richer tag set (memory_type:*, incident:*, location:*) rides
in `metadata.tags` for filtering and is mirrored on every local record.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from chronos import config
from chronos.state import MemoryResult

SUPERMEMORY_BASE = "https://api.supermemory.ai"
_STOP = set(
    "the a an of to in on at and or for is are be near my our i we it that this "
    "with from about previous prior calls call sop checklist".split()
)


@dataclass
class _Record:
    id: str
    content: str
    memory_type: str
    container_tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def tokens(self) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", self.content.lower()) if w not in _STOP}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def load_seed_records() -> list[_Record]:
    """Build memory records from the seed files in data/."""
    records: list[_Record] = []

    # SOP markdown -> one record per "# SOP:" section.
    sop_path = config.DATA_DIR / "seed_sops.md"
    if sop_path.exists():
        text = sop_path.read_text()
        sections = re.split(r"\n(?=#\s*SOP:)", text)
        for i, sec in enumerate(sections):
            sec = sec.strip()
            if not sec:
                continue
            title = sec.splitlines()[0].replace("#", "").strip()
            incident = "structure_fire"
            low = title.lower()
            if "vehicle" in low:
                incident = "vehicle_crash"
            elif "noise" in low:
                incident = "non_emergency_noise"
            elif "medical" in low:
                incident = "medical"
            records.append(
                _Record(
                    id=f"sop_{i}",
                    content=sec,
                    memory_type="sop",
                    container_tags=[config.AGENCY_TAG, "memory_type:sop", f"incident:{incident}"],
                    metadata={"source": "seed_sops", "title": title},
                )
            )

    for fname, mtype in [
        ("seed_prior_calls.json", "prior_call"),
        ("seed_location_memory.json", "location_alias"),
        ("seed_failure_memories.json", "eval_failure"),
    ]:
        path = config.DATA_DIR / fname
        if not path.exists():
            continue
        for item in json.loads(path.read_text()):
            records.append(
                _Record(
                    id=item.get("id", f"{mtype}_{len(records)}"),
                    content=item.get("summary", item.get("content", "")),
                    memory_type=mtype,
                    container_tags=item.get("container_tags")
                    or [config.AGENCY_TAG, f"memory_type:{mtype}"],
                    metadata=item.get("metadata", {}),
                )
            )
    return records


class ChronosMemoryClient:
    def __init__(self, api_key: str | None = None, force_local: bool = False) -> None:
        self.api_key = api_key
        self.online = bool(api_key) and config.USE_SUPERMEMORY and not force_local
        self.mode = "supermemory" if self.online else "local"
        self._records: list[_Record] = load_seed_records()
        self._index: dict[str, _Record] = {r.id: r for r in self._records}

    # --- internal HTTP (stdlib; no extra deps) ------------------------------
    def _post(self, path: str, body: dict[str, Any], timeout: float = 8.0) -> dict[str, Any] | None:
        url = f"{SUPERMEMORY_BASE}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            config_warn(f"Supermemory POST {path} failed: {e}")
            return None

    # --- writes -------------------------------------------------------------
    def add(
        self,
        content: str,
        container_tags: list[str],
        metadata: dict[str, Any] | None = None,
        memory_type: str = "note",
        custom_id: str | None = None,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        metadata.setdefault("tags", container_tags)
        metadata.setdefault("memory_type", memory_type)
        rec = _Record(
            id=custom_id or f"{memory_type}_{len(self._records)}",
            content=content,
            memory_type=memory_type,
            container_tags=container_tags,
            metadata=metadata,
        )
        # Update the local store (so it's immediately searchable / deterministic).
        self._index[rec.id] = rec
        if rec.id not in {r.id for r in self._records}:
            self._records.append(rec)
        result = {"id": rec.id, "mode": "local"}
        if self.online:
            # Supermemory accepts a single containerTag; the full tag set rides in
            # metadata.tags (containerTags is deprecated and rejected if both sent).
            body = {
                "content": content,
                "containerTag": config.AGENCY_TAG,
                "customId": rec.id,
                "metadata": _scalar_meta({**metadata, "tags": container_tags}),
            }
            resp = self._post("/v3/documents", body)
            if resp:
                result = {"id": resp.get("id", rec.id), "mode": "supermemory", "status": resp.get("status")}
        return result

    def seed(self) -> dict[str, Any]:
        """Push all seed records to Supermemory (if online). Local is preloaded.

        Pushes run CONCURRENTLY (thread pool) so seeding ~10 docs is a few seconds,
        not ~10×timeout seconds — keeps the dashboard 'Seed' button responsive.
        """
        pushed = 0
        if self.online:
            from concurrent.futures import ThreadPoolExecutor

            def _push(r: _Record) -> bool:
                body = {
                    "content": r.content,
                    "containerTag": config.AGENCY_TAG,
                    "customId": r.id,
                    "metadata": _scalar_meta({**r.metadata, "tags": r.container_tags, "memory_type": r.memory_type}),
                }
                return bool(self._post("/v3/documents", body, timeout=6.0))

            with ThreadPoolExecutor(max_workers=min(10, len(self._records) or 1)) as ex:
                pushed = sum(1 for ok in ex.map(_push, self._records) if ok)
        return {"mode": self.mode, "local_records": len(self._records), "pushed_to_supermemory": pushed}

    def write_call_summary(self, snapshot: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
        inc = snapshot.get("incident", {})
        content = (
            f"Call summary ({snapshot.get('call_id')}): incident={inc.get('incident_type')}, "
            f"risk={inc.get('risk_level')}, location={inc.get('location_raw')}, "
            f"hazards={inc.get('hazards')}, escalation={inc.get('escalation_required')}. "
            f"Transcript turns: {' | '.join(snapshot.get('turns', []))}"
        )
        tags = [config.AGENCY_TAG, "memory_type:prior_call", f"call:{snapshot.get('call_id')}"]
        if inc.get("incident_type"):
            tags.append(f"incident:{inc['incident_type']}")
        return self.add(content, tags, {"source": "call_summary"}, "prior_call", f"call_{snapshot.get('call_id')}")

    def write_failure_memory(
        self, failure: dict[str, Any], patch: dict[str, Any], regression: dict[str, Any]
    ) -> dict[str, Any]:
        content = (
            f"Learned rule: {failure.get('root_cause', failure.get('summary', ''))} "
            f"Patch applied to {patch.get('target_file')}: {patch.get('why_this_fixes_it', '')}"
        )
        tags = [
            config.AGENCY_TAG,
            "memory_type:eval_failure",
            f"incident:{failure.get('incident_type', 'unknown')}",
            f"failure_type:{failure.get('failure_type', 'unknown')}",
        ]
        meta = {
            "source": "cekura_eval",
            "scenario_id": failure.get("scenario_id"),
            "patch_file": patch.get("target_file"),
            "before_pass_rate": regression.get("before_pass_rate"),
            "after_pass_rate": regression.get("after_pass_rate"),
        }
        return self.add(content, tags, meta, "eval_failure", f"failmem_{failure.get('scenario_id', 'x')}")

    # --- reads --------------------------------------------------------------
    def search_local(
        self, query: str, container_tags: list[str] | None = None, limit: int = 5, threshold: float = 0.0
    ) -> list[MemoryResult]:
        q = query
        # Expand {location_raw} placeholders defensively (callers usually pre-fill).
        qtokens = _tokens(q)
        want_tags = set(container_tags or [])
        scored: list[tuple[float, _Record]] = []
        for r in self._records:
            overlap = len(qtokens & r.tokens())
            denom = (len(qtokens) or 1)
            score = overlap / denom
            # Tag/location boosts.
            if want_tags & set(r.container_tags):
                score += 0.4
            for tag in r.container_tags:
                if tag.startswith("location:"):
                    loc = tag.split(":", 1)[1].replace("_", " ")
                    if any(tok in q.lower() for tok in loc.split()):
                        score += 0.3
            if r.memory_type == "eval_failure" and ("failure" in q.lower() or "branch" in q.lower()):
                score += 0.2
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[MemoryResult] = []
        for score, r in scored[:limit]:
            if score < threshold:
                continue
            out.append(
                MemoryResult(
                    id=r.id,
                    content=r.content,
                    score=round(min(score, 1.0), 3),
                    memory_type=r.memory_type,
                    container_tags=r.container_tags,
                    metadata=r.metadata,
                )
            )
        return out

    async def search(
        self, query: str, container_tags: list[str] | None = None, limit: int = 5, threshold: float = 0.55
    ) -> list[MemoryResult]:
        """Async search used by the live voice path. Tries Supermemory, then
        always merges the deterministic local results so the demo is resilient."""
        results: list[MemoryResult] = []
        if self.online:
            try:
                import aiohttp

                # Supermemory v4 search: `q` + searchMode=hybrid (searches extracted
                # memories, then falls back to document chunks). Results carry a
                # `memory` or `chunk` field and a `similarity` score.
                body = {
                    "q": query,
                    "containerTag": config.AGENCY_TAG,
                    "searchMode": "hybrid",
                    "threshold": threshold,
                    "limit": limit,
                }
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{SUPERMEMORY_BASE}/v4/search", json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=4)
                    ) as resp:
                        if resp.status == 200:
                            payload = await resp.json()
                            for item in payload.get("results", []) or []:
                                meta = item.get("metadata", {}) or {}
                                content = item.get("memory") or item.get("chunk") or item.get("content") or ""
                                results.append(
                                    MemoryResult(
                                        id=str(item.get("id") or item.get("documentId") or content[:24]),
                                        content=content,
                                        score=float(item.get("similarity", item.get("score", 0.0)) or 0.0),
                                        memory_type=meta.get("memory_type", "supermemory"),
                                        container_tags=meta.get("tags", [config.AGENCY_TAG]),
                                        metadata=meta,
                                    )
                                )
            except Exception as e:  # noqa: BLE001
                config_warn(f"Supermemory search failed, using local: {type(e).__name__}: {e}")

        # Merge local results, deduping by id AND by content (the same memory has
        # different ids in Supermemory vs the local store), preserve order.
        def _ckey(c: str) -> str:
            return " ".join(c.lower().split())[:80]

        seen_ids = {r.id for r in results}
        seen_content = {_ckey(r.content) for r in results}
        for r in self.search_local(query, container_tags, limit, threshold=0.0):
            if r.id in seen_ids or _ckey(r.content) in seen_content or not r.content.strip():
                continue
            results.append(r)
            seen_ids.add(r.id)
            seen_content.add(_ckey(r.content))
        return results[:limit]

    async def search_many(
        self, queries: list[str], container_tags: list[str] | None = None, limit: int = 5, max_queries: int = 4
    ) -> list[MemoryResult]:
        # Run queries CONCURRENTLY (and cap them) so a turn's retrieval stays fast
        # even when each Supermemory call has a multi-second timeout.
        queries = queries[:max_queries]
        import asyncio

        batches = await asyncio.gather(
            *(self.search(q, container_tags, limit) for q in queries), return_exceptions=True
        )
        out: list[MemoryResult] = []
        seen: set[str] = set()
        for batch in batches:
            if isinstance(batch, Exception):
                continue
            for r in batch:
                if r.id not in seen:
                    out.append(r)
                    seen.add(r.id)
        return out


def _scalar_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Keep only metadata values Supermemory accepts (scalars or lists of them)."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, list):
            out[k] = [x for x in v if isinstance(x, (str, int, float, bool))]
    return out


def config_warn(msg: str) -> None:
    try:
        from loguru import logger

        logger.warning(msg)
    except Exception:
        print(f"[chronos.memory] {msg}")
