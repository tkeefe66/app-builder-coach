"""Capability tagging: cheap heuristics + cache-forever Haiku classification."""
from pathlib import Path
import hashlib
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

import yaml

# The canonical reporter already lives in this repo; import it rather than
# copying it in, so there is exactly one source of truth for the coach too.
from reporters.usage import report

log = logging.getLogger("classifier")

APP_SLUG = "app-builder-coach"          # must match a `name` in apps.yaml
HAIKU_MODEL = "claude-haiku-4-5-20251001"
PRICE_IN_PER_MTOK = 1.00
PRICE_OUT_PER_MTOK = 5.00


def load_taxonomy(path: Path) -> dict:
    tax = yaml.safe_load(path.read_text())
    return {"tags": list(tax["tags"]), "heuristics": dict(tax.get("heuristics") or {})}


def heuristic_tags(files: list[str], message: str, taxonomy: dict) -> list[str]:
    haystack = " ".join(files + [message]).lower()
    hits = {tag for needle, tag in taxonomy["heuristics"].items()
            if needle.lower() in haystack and tag in taxonomy["tags"]}
    return sorted(hits)


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_units(ledger_rows: list[dict], spec_rows: list[dict],
                exclude_month: str | None = None) -> list[dict]:
    units = []
    repos_with_specs = {s["repo"] for s in spec_rows}
    for s in spec_rows:
        body = s.get("body", "")
        units.append({"kind": "spec", "repo": s["repo"], "date": s["date"],
                      "title": s["title"],
                      "text": f"{s['title']}\n{s['spec_path']}\n{body}"})
    clusters: dict[tuple, list[dict]] = defaultdict(list)
    for row in ledger_rows:
        if row["repo"] in repos_with_specs:
            continue
        month = row["date"][:7]
        clusters[(row["repo"], month)].append(row)
    for (repo, month), rows in sorted(clusters.items()):
        if exclude_month is not None and month == exclude_month:
            continue
        text = "\n".join(f"{r['message']} | {' '.join(r['files'][:20])}" for r in rows)
        units.append({"kind": "commits", "repo": repo, "date": f"{month}-01",
                      "title": f"{repo} {month}", "text": text})
    return units


_PROMPT = """You classify a software feature into capability tags.
Allowed tags (choose 1-4, ONLY from this list): {tags}
Feature description:
{text}

Reply with ONLY a JSON object: {{"tags": [...], "complexity": 1-5, "summary": "one line"}}
complexity: 1=config tweak, 3=solid feature, 5=multi-system architecture."""


def classify_unit(text: str, taxonomy: dict, client) -> dict:
    resp = client.messages.create(
        model=HAIKU_MODEL, max_tokens=300,
        messages=[{"role": "user", "content": _PROMPT.format(
            tags=", ".join(taxonomy["tags"]), text=text[:6000])}],
    )
    # Report before parsing: a malformed reply still cost real tokens.
    report(APP_SLUG, HAIKU_MODEL, resp.usage)
    raw = resp.content[0].text
    match = _JSON_BLOCK.search(raw)
    data = json.loads(match.group(0)) if match else {}
    tags = [t for t in data.get("tags", []) if t in taxonomy["tags"]]
    return {"tags": tags,
            "complexity": int(data.get("complexity", 2)),
            "summary": str(data.get("summary", ""))[:200],
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens}


def read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for i, line in enumerate(path.read_text().splitlines()):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("skipping malformed jsonl line %d in %s", i, path)
            continue
    return rows


_read_jsonl = read_jsonl


def effective_rows(rows: list[dict]) -> list[dict]:
    """Collapse tiered cache rows to one row per base content hash, preferring
    a model (':m') row over a heuristics (':h') row for the same content."""
    by_base: dict[str, dict] = {}
    for row in rows:
        key = row.get("key", "")
        base = key.split(":", 1)[0]
        existing = by_base.get(base)
        if existing is None or (key.endswith(":m") and not existing.get("key", "").endswith(":m")):
            by_base[base] = row
    return list(by_base.values())


def run_classifier(data_dir: Path, taxonomy: dict, client_factory) -> dict:
    ledger = read_jsonl(data_dir / "ledger.jsonl")
    specs = read_jsonl(data_dir / "specs.jsonl")
    done = {row["key"] for row in read_jsonl(data_dir / "classifications.jsonl")}
    client = client_factory()
    result = {"classified": 0, "cached": 0, "failed": 0}
    out_path = data_dir / "classifications.jsonl"
    cost_path = data_dir / "llm_costs.jsonl"
    exclude_month = datetime.now(timezone.utc).strftime("%Y-%m")
    for unit in build_units(ledger, specs, exclude_month=exclude_month):
        base_hash = content_hash(unit["text"])
        tier = "m" if client is not None else "h"
        key = f"{base_hash}:{tier}"
        if key in done or f"{base_hash}:m" in done:
            result["cached"] += 1
            continue
        if client is None:
            if unit["kind"] == "commits":
                files = [p for r in ledger if r["repo"] == unit["repo"]
                        and r["date"][:7] == unit["date"][:7] for p in r["files"]]
            else:
                files = []
            row = {"tags": heuristic_tags(files, unit["text"], taxonomy),
                   "complexity": 2, "summary": unit["title"], "model": "heuristics"}
        else:
            try:
                got = classify_unit(unit["text"], taxonomy, client)
            except Exception:
                result["failed"] += 1
                continue
            row = {"tags": got["tags"], "complexity": got["complexity"],
                   "summary": got["summary"], "model": HAIKU_MODEL}
            cost = (got["input_tokens"] * PRICE_IN_PER_MTOK
                    + got["output_tokens"] * PRICE_OUT_PER_MTOK) / 1_000_000
            with cost_path.open("a") as fh:
                fh.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "unit": unit["title"],
                    "input_tokens": got["input_tokens"],
                    "output_tokens": got["output_tokens"],
                    "cost_usd": round(cost, 6)}) + "\n")
        with out_path.open("a") as fh:
            fh.write(json.dumps({"key": key, "kind": unit["kind"],
                                 "repo": unit["repo"], "date": unit["date"],
                                 "title": unit["title"], **row},
                                sort_keys=True) + "\n")
        done.add(key)
        result["classified"] += 1
    return result
