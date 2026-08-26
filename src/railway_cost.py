"""Railway infra cost lane: per-app period-to-date dollars from the railway CLI.

The CLI reports billing-period-to-date totals, not a daily series, so this lane
ships cumulative values and the server derives daily deltas.
"""
import json
import logging
import subprocess

from shared.apps import by_railway_id

log = logging.getLogger("railway_cost")


def fetch_usage(run=subprocess.run, project: str | None = None) -> dict | None:
    """Return the parsed `railway usage projects` payload, or None.

    With `project` (a Railway project id), returns that project's per-service
    breakdown instead of every project's total. Every failure mode returns None
    so the sweep can degrade: the CLI may be missing, unauthenticated (non-zero
    exit), or slow.
    """
    cmd = ["railway", "usage", "projects"]
    if project:
        cmd += ["--project", project]
    cmd.append("--json")
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        log.exception("railway CLI could not be run")
        return None
    if proc.returncode != 0:
        log.warning("railway usage exited %s: %s",
                    proc.returncode, (proc.stderr or "")[:200])
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning("railway usage returned non-JSON output")
        return None


def infra_rows(payload: dict | None, apps: list[dict],
               capture_date: str) -> list[dict]:
    if not payload:
        return []
    period_start = str((payload.get("billingPeriod") or {}).get("start") or "")[:10]
    if len(period_start) != 10:
        log.warning("railway usage payload has no usable billingPeriod.start")
        return []
    mapping = by_railway_id(apps)
    rows = []
    for project in payload.get("projects") or []:
        name = mapping.get(project.get("id"))
        if name is None:
            continue  # a Railway project with no registry entry is not an app we track
        rows.append({
            "capture_date": capture_date,
            "period_start": period_start,
            "app": name,
            "cumulative_usd": round(float(project.get("currentUsageDollars") or 0.0), 6),
        })
    return sorted(rows, key=lambda r: r["app"])


# Railway field -> our row field. Railway reports unused components as -0.0.
_SERVICE_FIELDS = (
    ("totalDollars", "cumulative_usd"),
    ("memoryDollars", "memory_usd"),
    ("cpuDollars", "cpu_usd"),
    ("egressDollars", "egress_usd"),
    ("volumeDollars", "volume_usd"),
    ("backupDollars", "backup_usd"),
)


def _dollars(raw) -> float:
    value = round(float(raw or 0.0), 6)
    # -0.0 survives rounding and its repr is "-0.0", which is meaningless to
    # display and poisons downstream sums and charts. Collapse it to 0.0.
    return 0.0 if value == 0 else value


def service_rows(payload: dict | None, app: str, capture_date: str,
                 expected_project_id: str) -> list[dict] | None:
    """Per-service rows for one project's payload.

    Returns:
        - None if the payload is unusable (missing, wrong project ID, bad billing period).
          This indicates the project could not be read.
        - [] (empty list) if the payload is valid but has no services to report.
          This distinction allows collect_service_rows to count the project as successfully
          read, even if it legitimately has zero services.
    """
    if not payload:
        return None
    got_id = (payload.get("project") or {}).get("id")
    if got_id != expected_project_id:
        log.warning("railway returned project %r when %r was requested; dropping",
                    got_id, expected_project_id)
        return None
    period_start = str((payload.get("billingPeriod") or {}).get("start") or "")[:10]
    if len(period_start) != 10:
        log.warning("project %s payload has no usable billingPeriod.start", app)
        return None
    rows = []
    for svc in payload.get("services") or []:
        service_id = svc.get("id")
        if not service_id:
            continue
        row = {"capture_date": capture_date, "period_start": period_start,
               "app": app, "service_id": str(service_id),
               "service_name": str(svc.get("name") or "")}
        for src_field, dst_field in _SERVICE_FIELDS:
            row[dst_field] = _dollars(svc.get(src_field))
        rows.append(row)
    return sorted(rows, key=lambda r: r["service_id"])


def collect_service_rows(apps: list[dict], capture_date: str,
                         run=subprocess.run) -> tuple[list[dict], int, int]:
    """One CLI call per registry entry. Returns (rows, ok_count, attempted).

    Partial failure is the expected case with this many calls: one project
    failing must not withhold the others' drill-down detail.

    ok_count reflects projects successfully read (payload was usable), even if
    they have no services to report. This allows the count to distinguish between
    "one project failed" and "one project is legitimately empty".
    """
    rows: list[dict] = []
    ok = 0
    apps = list(apps)
    for app in apps:
        project_id = app["railway_project_id"]
        payload = fetch_usage(run=run, project=project_id)
        got = service_rows(payload, app["name"], capture_date, project_id)
        if got is not None:
            ok += 1
            rows.extend(got)
    return rows, ok, len(apps)
