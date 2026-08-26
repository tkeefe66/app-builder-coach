"""Snapshot payload contract between local sweep and coach-web server."""
import hashlib
import json

SCHEMA_VERSION = 4
SUPPORTED_VERSIONS = (1, 2, 3, 4)
REQUIRED_KEYS = ("schema_version", "sweep", "feature_units",
                 "activity_daily", "adoption")
# v2 adds cost_daily; v3 adds infra_usage; v4 adds infra_usage_services. Older
# payloads must keep validating forever: the outbox can hold them, and a
# rejection quarantines them permanently.
V2_REQUIRED_KEYS = REQUIRED_KEYS + ("cost_daily",)
V3_REQUIRED_KEYS = V2_REQUIRED_KEYS + ("infra_usage",)
V4_REQUIRED_KEYS = V3_REQUIRED_KEYS + ("infra_usage_services",)

# Per-list item contracts: (required keys, keys allowed at all).
# The content hash proves a payload arrived intact, not that it is
# well-formed -- a client bug produces a correctly-hashed bad row, which
# must be a 400 rather than an exploding INSERT.
UNIT_KEYS = {"key", "kind", "repo", "date", "title",
             "tags", "complexity", "summary", "model"}
COST_KEYS = {"date", "input_tokens", "output_tokens", "cache_read_tokens",
             "cache_creation_tokens", "cost_usd", "by_model"}
ITEM_SCHEMAS = {
    "feature_units": (UNIT_KEYS, UNIT_KEYS),
    "activity_daily": ({"date", "commits", "by_repo"},
                       {"date", "commits", "by_repo", "sessions", "prompts"}),
    "adoption": ({"name", "status"}, {"name", "lesson", "status", "last_used"}),
}
# Validated only for v2 payloads (v1 has no cost_daily key at all).
V2_ITEM_SCHEMAS = {"cost_daily": (COST_KEYS, COST_KEYS)}
# (list name, field, expected type, human name)
FIELD_TYPES = (
    ("feature_units", "tags", list, "list"),
    ("feature_units", "complexity", int, "int"),
    ("activity_daily", "commits", int, "int"),
    ("activity_daily", "by_repo", dict, "object"),
)
V2_FIELD_TYPES = (
    ("cost_daily", "input_tokens", int, "int"),
    ("cost_daily", "output_tokens", int, "int"),
    ("cost_daily", "cache_read_tokens", int, "int"),
    ("cost_daily", "cache_creation_tokens", int, "int"),
    ("cost_daily", "cost_usd", (int, float), "number"),
    ("cost_daily", "by_model", dict, "object"),
)
INFRA_KEYS = {"capture_date", "period_start", "app", "cumulative_usd"}
V3_ITEM_SCHEMAS = {"infra_usage": (INFRA_KEYS, INFRA_KEYS)}
V3_FIELD_TYPES = (
    ("infra_usage", "cumulative_usd", (int, float), "number"),
)
SERVICE_KEYS = {"capture_date", "period_start", "app", "service_id", "service_name",
                "cumulative_usd", "memory_usd", "cpu_usd", "egress_usd",
                "volume_usd", "backup_usd"}
V4_ITEM_SCHEMAS = {"infra_usage_services": (SERVICE_KEYS, SERVICE_KEYS)}
V4_FIELD_TYPES = tuple(
    ("infra_usage_services", field, (int, float), "number")
    for field in ("cumulative_usd", "memory_usd", "cpu_usd",
                  "egress_usd", "volume_usd", "backup_usd")
)


def canonical_hash(body: dict) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def finalize_payload(body: dict, captured_at: str) -> dict:
    return {**body, "captured_at": captured_at, "content_hash": canonical_hash(body)}


def validate_payload(p: dict) -> None:
    if "schema_version" not in p:
        raise ValueError("snapshot missing key: schema_version")
    version = p["schema_version"]
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"unsupported schema_version {version!r}, "
            f"expected one of {SUPPORTED_VERSIONS}")
    if version == 4:
        required = V4_REQUIRED_KEYS
    elif version == 3:
        required = V3_REQUIRED_KEYS
    elif version == 2:
        required = V2_REQUIRED_KEYS
    else:
        required = REQUIRED_KEYS
    for key in required + ("captured_at", "content_hash"):
        if key not in p:
            raise ValueError(f"snapshot missing key: {key}")
    if version == 1 and "cost_daily" in p:
        raise ValueError("schema_version 1 must not carry cost_daily")
    if version < 3 and "infra_usage" in p:
        raise ValueError(f"schema_version {version} must not carry infra_usage")
    if version < 4 and "infra_usage_services" in p:
        raise ValueError(
            f"schema_version {version} must not carry infra_usage_services")
    body = {k: v for k, v in p.items() if k not in ("captured_at", "content_hash")}
    if canonical_hash(body) != p["content_hash"]:
        raise ValueError("content_hash mismatch: payload corrupted or tampered")
    _validate_items(p, ITEM_SCHEMAS, FIELD_TYPES)
    if version >= 2:
        _validate_items(p, V2_ITEM_SCHEMAS, V2_FIELD_TYPES)
    if version >= 3:
        _validate_items(p, V3_ITEM_SCHEMAS, V3_FIELD_TYPES)
    if version >= 4:
        _validate_items(p, V4_ITEM_SCHEMAS, V4_FIELD_TYPES)


def _validate_items(p: dict, item_schemas: dict, field_types: tuple) -> None:
    """Check every row of each list against its key/type contract."""
    for name, (required, allowed) in item_schemas.items():
        rows = p[name]
        if not isinstance(rows, list):
            raise ValueError(f"{name} must be a list, got {type(rows).__name__}")
        for i, row in enumerate(rows):
            where = f"{name}[{i}]"
            if not isinstance(row, dict):
                raise ValueError(f"{where} must be an object, "
                                 f"got {type(row).__name__}")
            missing = sorted(required - row.keys())
            if missing:
                label = "key" if len(missing) == 1 else "keys"
                raise ValueError(f"{where} missing required {label}: "
                                 f"{', '.join(missing)}")
            for key in sorted(row.keys() - allowed):
                raise ValueError(f"{where} has unexpected key: {key}")

    for name, field, expected, label in field_types:
        for i, row in enumerate(p[name]):
            value = row[field]
            # bool is an int subclass; a flag is never a valid count.
            if not isinstance(value, expected) or isinstance(value, bool):
                raise ValueError(f"{name}[{i}].{field} must be {label}, "
                                 f"got {type(value).__name__}")
