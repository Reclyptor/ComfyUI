"""Rebuild the job history that the Media Assets "Generated" tab renders.

ComfyUI keeps prompt history in memory (`execution.PromptQueue.history`), and
`/api/jobs` -- the endpoint that tab reads -- is assembled from it in
`server.py:get_jobs`. Nothing persists it. The frontend does have a durable
path, but it is gated on deployment type:

    fetchGeneratedAssets({isCloud, ...}) =>
        isCloud ? fetchCloudGeneratedAssets(...)    # reads /api/assets
                : fetchGeneratedHistoryAssets(...)  # reads in-memory history

Self-hosted takes the second branch, so every restart empties the tab even
though the PNGs and their asset rows are perfectly intact.

`--enable-assets` records a `job_id` on every output an execution produces, so
the job -> outputs mapping is recoverable from `asset_references`. This replays
those groups back into the history as completed jobs at import time. The job ids
and timestamps are the real ones recorded at execution; the only synthesised
part is an empty prompt graph, which that view never displays.

Requires `--enable-assets`. Without it there are no job_ids to read and this
does nothing. Anything generated before the flag was enabled has no job to be
grouped under and is deliberately not invented.

Env:
    COMFYUI_REHYDRATE_LIMIT   most recent jobs to restore (default 1000, 0 = off)
"""

import json
import logging
import os
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

log = logging.getLogger("history-rehydrate")

_QUERY = """
    SELECT job_id, name, file_path, user_metadata, system_metadata, created_at
    FROM asset_references
    WHERE job_id IS NOT NULL
      AND deleted_at IS NULL
      AND is_missing = 0
    ORDER BY created_at ASC
"""


def _sqlite_path():
    """Resolve the assets DB, or None when it is not a local sqlite file."""
    from comfy.cli_args import args

    url = getattr(args, "database_url", "") or ""
    if not url.startswith("sqlite:///"):
        return None
    path = url[len("sqlite:///"):]
    return path if os.path.isfile(path) else None


def _epoch_ms(stamp):
    """DB timestamps are naive UTC; the frontend wants epoch milliseconds."""
    try:
        dt = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _relative_name(row_name, user_metadata, file_path):
    """Recover "<subfolder>/<filename>" as SaveImage reported it.

    user_metadata.filename already carries the subfolder; fall back to the path
    relative to the output root, then to the bare name.
    """
    try:
        meta = json.loads(user_metadata) if user_metadata else {}
        if isinstance(meta, dict) and meta.get("filename"):
            return meta["filename"]
    except (TypeError, ValueError):
        pass

    if file_path:
        try:
            import folder_paths

            rel = os.path.relpath(file_path, folder_paths.get_output_directory())
            if not rel.startswith(".."):
                return rel
        except Exception:
            pass
    return row_name


def _collect(limit):
    """Group output assets by the job that produced them, oldest job first."""
    path = _sqlite_path()
    if not path:
        return OrderedDict()

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(_QUERY).fetchall()
    finally:
        conn.close()

    jobs = OrderedDict()
    for job_id, name, file_path, user_meta, sys_meta, created_at in rows:
        try:
            info = json.loads(sys_meta) if sys_meta else {}
        except (TypeError, ValueError):
            info = {}
        if info.get("kind") not in (None, "image"):
            continue  # the tab previews images; skip anything else

        rel = _relative_name(name, user_meta, file_path)
        subfolder, _, filename = rel.rpartition("/")

        job = jobs.setdefault(job_id, {"images": [], "first": None, "last": None})
        job["images"].append(
            {"filename": filename, "subfolder": subfolder, "type": "output"}
        )
        ms = _epoch_ms(created_at)
        if ms is not None:
            if job["first"] is None:
                job["first"] = ms
            job["last"] = ms

    if limit and len(jobs) > limit:
        keep = list(jobs)[-limit:]
        jobs = OrderedDict((k, jobs[k]) for k in keep)
    return jobs


def _as_history_entry(job_id, job):
    start = job["first"]
    end = job["last"] or start
    return {
        # normalize_history_item unpacks exactly five elements
        "prompt": (0, job_id, {}, {"create_time": start}, []),
        "outputs": {"0": {"images": job["images"]}},
        "status": {
            "status_str": "success",
            "completed": True,
            "messages": [
                ["execution_start", {"prompt_id": job_id, "timestamp": start}],
                ["execution_success", {"prompt_id": job_id, "timestamp": end}],
            ],
        },
    }


def rehydrate():
    """Replay persisted jobs into the live queue's history. Returns count."""
    try:
        limit = int(os.environ.get("COMFYUI_REHYDRATE_LIMIT", "1000"))
    except ValueError:
        limit = 1000
    if limit == 0:
        log.info("[history-rehydrate] disabled via COMFYUI_REHYDRATE_LIMIT=0")
        return 0

    from server import PromptServer

    queue = getattr(PromptServer.instance, "prompt_queue", None)
    if queue is None:
        log.warning("[history-rehydrate] prompt_queue not ready; skipping")
        return 0

    jobs = _collect(limit)
    if not jobs:
        log.info("[history-rehydrate] no persisted jobs found "
                 "(is --enable-assets set?)")
        return 0

    restored = 0
    with queue.mutex:
        for job_id, job in jobs.items():
            if job_id in queue.history:
                continue  # never clobber a job from this session
            queue.history[job_id] = _as_history_entry(job_id, job)
            restored += 1

    log.info("[history-rehydrate] restored %d job(s), %d image(s)",
             restored, sum(len(j["images"]) for j in jobs.values()))
    return restored


try:
    rehydrate()
except Exception:
    # Never take startup down for a history nicety. A ComfyUI upgrade that
    # reshapes PromptQueue.history or the assets schema lands here, and the
    # tab simply falls back to being session-scoped.
    log.exception("[history-rehydrate] failed; job history stays session-scoped")
