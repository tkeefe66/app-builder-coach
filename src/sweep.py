"""Daily sweep: collect -> classify -> profile. Always exits 0."""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from shared import apps as apps_registry
from . import adoption, classifier, collector, config, profile, railway_cost, shipper
from . import usage as usage_lane  # aliased: main() binds a local `usage` below

log = logging.getLogger("sweep")


def _client_factory():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def main(root: Path | None = None, data_dir: Path | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    config.load_env()
    root = root or config.code_apps_root()
    data_dir = data_dir or config.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()

    if not root.is_dir():
        print(f"sweep: root {root} missing; repos=0 new_commits=0")
        return 0

    try:
        swept = collector.sweep(root, data_dir)
        for err in swept["errors"]:
            log.warning("collect error: %s", err)

        taxonomy = classifier.load_taxonomy(config.REPO_ROOT / "taxonomy.yaml")
        classed = classifier.run_classifier(data_dir, taxonomy, _client_factory)

        home = Path.home() / ".claude"
        usage, history_skipped = adoption.parse_history_commands(home / "history.jsonl")
        inventory = adoption.inventory_config(home / "settings.json", home / "skills")
        features = adoption.load_checklist(config.REPO_ROOT / "feature-checklist.yaml")
        adoption_rows = adoption.evaluate_checklist(features, usage, inventory)

        cls_rows = classifier.effective_rows(
            classifier.read_jsonl(data_dir / "classifications.jsonl"))
        ledger_len = len(classifier.read_jsonl(data_dir / "ledger.jsonl"))
        matrix = profile.build_matrix(cls_rows, taxonomy["tags"], today)
        text = profile.render(matrix, adoption_rows,
                              {"generated": today, "commits": ledger_len,
                               "repos": swept["repos"],
                               "history_skipped": history_skipped})
        profile.write_profile(data_dir, text)

        try:
            usage_data = usage_lane.daily_rollups(
                usage_lane.scan_projects(Path.home() / ".claude", data_dir))
        except Exception:
            log.exception("usage lane failed; shipping without usage data")
            usage_data = {"activity": {}, "cost": []}

        try:
            infra_rows = railway_cost.infra_rows(
                railway_cost.fetch_usage(),
                apps_registry.load_apps(config.REPO_ROOT / "apps.yaml"),
                today)
        except Exception:
            log.exception("infra lane failed; shipping without infra data")
            infra_rows = []

        try:
            service_rows, svc_ok, svc_n = railway_cost.collect_service_rows(
                apps_registry.load_apps(config.REPO_ROOT / "apps.yaml"), today)
        except Exception:
            log.exception("infra service lane failed; shipping without service data")
            service_rows, svc_ok, svc_n = [], 0, 0

        ingest_url = os.environ.get("COACH_INGEST_URL")
        ship_stats = None
        if ingest_url:
            payload = shipper.build_snapshot(
                data_dir, adoption_rows,
                {"repos": swept["repos"], "new_commits": swept["new_commits"],
                 "specs": swept["specs"], "errors": len(swept["errors"])},
                captured_at=datetime.now(timezone.utc).isoformat(),
                usage=usage_data, infra=infra_rows, services=service_rows)
            ship_stats = shipper.ship_all(
                payload, ingest_url, os.environ.get("COACH_INGEST_TOKEN", ""),
                data_dir / "outbox")

        print(f"sweep: repos={swept['repos']} new_commits={swept['new_commits']} "
              f"specs={swept['specs']} classified={classed['classified']} "
              f"cached={classed['cached']} failed={classed['failed']} "
              f"errors={len(swept['errors'])} "
              f"infra={'ok' if infra_rows else 'failed'} "
              f"infra_services={'ok' if svc_ok and svc_ok == svc_n else ('failed' if not svc_ok else f'partial({svc_ok}/{svc_n})')}"
              + (f" shipped={ship_stats['shipped']} queued={ship_stats['queued']}"
                 + (f" rejected={ship_stats['rejected']}"
                    if ship_stats['rejected'] else "")
                 if ship_stats else ""))
    except Exception as exc:
        log.exception("sweep stage failed")
        print(f"sweep: FAILED {type(exc).__name__}: {str(exc)[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
