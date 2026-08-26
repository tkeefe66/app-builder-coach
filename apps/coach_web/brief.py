"""Generate the coaching brief: a standing assessment plus short deltas.

A change fingerprint gates whether any model call happens at all. When it
fires, a deep "assessment" pass (Sonnet 5) over the whole corpus runs on the
first call and every `MAX_DELTAS_BEFORE_REASSESS`'th change after; every
change in between gets a short "delta" amendment (Haiku 4.5) against the
standing assessment instead of a fresh essay. Context building is a pure
function over DB rows so it can be tested without an API key or a network.
The Anthropic client is injected for the same reason.
"""
import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from . import gaps, grade as grade_mod, models, rubric

log = logging.getLogger("brief")

DEFAULT_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024
STALE_DAYS = 180

FP_KEY = "brief.fingerprint"
DELTA_COUNT_KEY = "brief.deltas_since_assessment"
# Window for "what moved recently" in a delta's change description. Named for
# spend until 2026-08-12, when spend left the fingerprint; it never windowed
# spend in describe_change, only feature units and check-offs.
RECENT_WINDOW_DAYS = 7

MAX_RECENT_UNITS = 150
MAX_COMPLEX_UNITS = 50
ASSESSMENT_MAX_TOKENS = 16000


def get_state(db, key: str, default: str = "") -> str:
    row = db.get(models.WatcherState, key)
    return row.value if row is not None else default


def set_state(db, key: str, value: str, now: datetime | None = None) -> None:
    """Upsert a watcher_state key. Caller commits."""
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    row = db.get(models.WatcherState, key)
    if row is None:
        db.add(models.WatcherState(key=key, value=value, updated_at=stamp))
    else:
        row.value = value
        row.updated_at = stamp


def fingerprint(db, today: date) -> str:
    """Hash of the facts that make a new brief worth generating.

    Every component is a set, a count, or a watermark, so it moves on a real
    event and not on ordinary drift. An unchanged fingerprint means no model
    call at all -- this is the whole cost control.

    Spend is deliberately NOT a component. It was one until 2026-08-12, rounded
    to whole dollars on the theory that cents were the noise floor. Production
    said otherwise: trailing-7-day Claude Code spend runs in the thousands and
    moves by hundreds between sweeps, so the hash changed on literally every
    ingest and a delta fired every time -- the exact behaviour this gate exists
    to prevent. Spend is also an *outcome* of building, which `units` and `tags`
    already detect, so it was double-counting a signal we had. Do not add it
    back without a bucket coarse enough to survive a $3,000 day.
    """
    tags: set[str] = set()
    for (row_tags,) in db.execute(select(models.FeatureUnit.tags)):
        tags.update(row_tags or [])

    adopted: set[str] = set()
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    if latest is not None:
        adopted = set(db.scalars(
            select(models.AdoptionHistory.feature_name)
            .where(models.AdoptionHistory.snapshot_id == latest.id,
                   models.AdoptionHistory.status != "never-touched")))

    goals = sorted((g.id, g.status) for g in db.scalars(select(models.Goal)))
    units = db.scalar(select(func.count(models.FeatureUnit.key))) or 0

    body = {
        "tags": sorted(tags),
        "adopted": sorted(adopted),
        "goals": goals,
        "units": units,
        "changelog": get_state(db, "changelog.last_checked_at"),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _monthly(rows, date_attr: str, fields: tuple) -> list[dict]:
    """Bucket dated rows into {month: YYYY-MM, <field>: sum} entries."""
    buckets: dict[str, dict] = {}
    for r in rows:
        month = getattr(r, date_attr)[:7]
        b = buckets.setdefault(month, {"month": month,
                                       **{f: 0 for f in fields}})
        for f in fields:
            b[f] += getattr(r, f) or 0
    return [buckets[m] for m in sorted(buckets)]


def _gap_lists(db, today: date) -> tuple[list[str], list[str], list[str]]:
    """(never_built, stale_tags, adoption_gaps) as the coach sees them.

    Thin adapter over the shared helper: the coach excludes dismissed targets
    and wants stale as bare tags, while Overview keeps dismissals visible and
    renders stale with its last_done date.
    """
    out = gaps.gap_lists(db, today, exclude_dismissed=True)
    return (out["never_built"],
            [s["tag"] for s in out["stale"]],
            out["adoption_gaps"])


# Strongest-first: what matters about a target is whether it ever led anywhere.
_OUTCOME_RANK = {"converted": 3, "dismissed": 2, "open": 1, "superseded": 0}


def recommendation_history(db) -> list[dict]:
    """Per target: how often it was suggested, over what span, and what came of it."""
    rows = list(db.execute(
        select(models.BriefRecommendation.target, models.BriefRecommendation.kind,
               models.BriefRecommendation.outcome, models.Brief.day)
        .join(models.Brief, models.Brief.id == models.BriefRecommendation.brief_id)
        .order_by(models.Brief.day)))
    agg: dict[str, dict] = {}
    for target, kind, outcome, day in rows:
        e = agg.setdefault(target, {"target": target, "kind": kind, "times": 0,
                                    "first": "", "last": "", "outcome": outcome})
        e["times"] += 1
        # Only update first/last if day is non-empty
        if day:
            if not e["first"] or day < e["first"]:
                e["first"] = day
            if not e["last"] or day > e["last"]:
                e["last"] = day
        if _OUTCOME_RANK.get(outcome, 0) > _OUTCOME_RANK.get(e["outcome"], 0):
            e["outcome"] = outcome
    return [agg[t] for t in sorted(agg)]


def build_delta_context(db, today: date, assessment) -> dict:
    never_built, stale, adoption_gaps = _gap_lists(db, today)
    recs = list(db.scalars(
        select(models.BriefRecommendation)
        .where(models.BriefRecommendation.brief_id == assessment.id)
        .order_by(models.BriefRecommendation.ord)))
    return {
        "assessment_summary": assessment.body,
        "assessment_day": assessment.day,
        "assessment_recommendations": [
            {"title": r.title, "kind": r.kind, "target": r.target}
            for r in recs],
        "changed": describe_change(db, today),
        "history": recommendation_history(db),
        "never_built": never_built,
        "stale": stale,
        "adoption_gaps": adoption_gaps,
    }


def describe_change(db, today: date) -> list[str]:
    """Human-readable lines for what moved since the last brief.

    The fingerprint tells us *that* something changed; this tells the model
    *what*, so a delta can be specific instead of restating the assessment.
    """
    since = (today - timedelta(days=RECENT_WINDOW_DAYS)).isoformat()
    out = []
    fresh = list(db.scalars(select(models.FeatureUnit)
                            .where(models.FeatureUnit.date >= since)
                            .order_by(models.FeatureUnit.date)))
    for u in fresh:
        out.append(f"shipped [{u.date}] {u.repo}: {u.title} "
                   f"({', '.join(u.tags or []) or 'untagged'})")
    for g in db.scalars(select(models.Goal)
                       .where(models.Goal.status == "active")
                       .order_by(models.Goal.id)):
        out.append(f"goal ({g.status}): {g.title} -> {g.target}")
    for c in db.scalars(select(models.FeatureCheckoff)
                       .where(models.FeatureCheckoff.checked_at >= since)
                       .order_by(models.FeatureCheckoff.feature_name)):
        out.append(f"checked off: {c.feature_name}")
    return out


def _render_history(history: list[dict]) -> list[str]:
    """Lines for the '## Recommendation history' section.

    Shared by both prompts so the delta and assessment paths cannot drift
    apart on wording, the `fate` mapping, or the empty-case line.
    """
    lines = ["\n## Recommendation history"]
    for h in history:
        if h["outcome"] == "converted":
            fate = "became a goal"
        elif h["outcome"] == "dismissed":
            fate = "dismissed"
        else:
            fate = "never acted on"
        if h["first"]:
            lines.append(f"- {h['target']}: suggested {h['times']}x between "
                         f"{h['first']} and {h['last']}, {fate}")
        else:
            lines.append(f"- {h['target']}: suggested {h['times']}x, {fate}")
    if not history:
        lines.append("- (nothing suggested yet)")
    return lines


def render_delta_prompt(ctx: dict) -> str:
    def listing(items) -> str:
        return ", ".join(items) if items else "(none)"

    lines = [f"## Standing assessment ({ctx['assessment_day']})",
             ctx["assessment_summary"] or "(none)"]
    lines.append("\nIts open recommendations:")
    for r in ctx["assessment_recommendations"]:
        lines.append(f"- {r['title']} -> {r['target']}")
    if not ctx["assessment_recommendations"]:
        lines.append("- (none)")

    lines.append("\n## What has changed since")
    for c in ctx["changed"]:
        lines.append(f"- {c}")
    if not ctx["changed"]:
        lines.append("- (nothing material)")

    lines.extend(_render_history(ctx["history"]))

    lines.append("\n## Allowed recommendation targets")
    lines.append(f"tags never built: {listing(ctx['never_built'])}")
    lines.append(f"tags stale over {STALE_DAYS} days: {listing(ctx['stale'])}")
    lines.append(f"Claude Code features never adopted: {listing(ctx['adoption_gaps'])}")
    return "\n".join(lines)


def build_corpus_context(db, today: date) -> dict:
    """Everything the coach should know, as a pure function over DB rows.

    Replaces the six-numbers-and-three-lists context that made every brief
    identical. Pure so it stays testable without a key or a network.
    """
    units = list(db.scalars(select(models.FeatureUnit)
                            .order_by(models.FeatureUnit.date.desc(),
                                      models.FeatureUnit.key)))

    repos: dict[str, dict] = {}
    for u in units:
        r = repos.setdefault(u.repo, {"repo": u.repo, "units": 0, "cx_sum": 0,
                                      "first": u.date, "last": u.date,
                                      "tags": set()})
        r["units"] += 1
        r["cx_sum"] += u.complexity or 0
        r["first"] = min(r["first"], u.date)
        r["last"] = max(r["last"], u.date)
        r["tags"].update(u.tags or [])
    repo_list = []
    for r in sorted(repos.values(), key=lambda x: -x["units"]):
        repo_list.append({"repo": r["repo"], "units": r["units"],
                          "first": r["first"], "last": r["last"],
                          "mean_complexity": round(r["cx_sum"] / r["units"], 1),
                          "tags": sorted(r["tags"])})

    # Bound the corpus: most recent, plus the most complex not already picked.
    recent = units[:MAX_RECENT_UNITS]
    picked = {u.key for u in recent}
    complex_extra = sorted((u for u in units if u.key not in picked),
                           key=lambda u: (-(u.complexity or 0), u.date),
                           )[:MAX_COMPLEX_UNITS]
    kept = recent + complex_extra
    work = [{"repo": u.repo, "date": u.date, "title": u.title,
             "summary": u.summary, "complexity": u.complexity,
             "tags": u.tags or []} for u in kept]
    work_note = ""
    if len(kept) < len(units):
        work_note = (f"Showing {len(kept)} of {len(units)} units: the "
                     f"{len(recent)} most recent and the {len(complex_extra)} "
                     f"most complex of the rest.")

    unit_rows = [(u.repo, u.date, u.tags, u.complexity) for u in units]
    grade = grade_mod.compute_grade(unit_rows, rubric.load(), today)

    adoption = []
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    if latest is not None:
        adoption = [{"feature": a.feature_name, "status": a.status,
                     "last_used": a.last_used}
                    for a in db.scalars(
                        select(models.AdoptionHistory)
                        .where(models.AdoptionHistory.snapshot_id == latest.id)
                        .order_by(models.AdoptionHistory.feature_name))]

    commitments = {
        "goals": [{"id": g.id, "kind": g.kind, "target": g.target,
                   "title": g.title, "status": g.status}
                  for g in db.scalars(select(models.Goal)
                                      .order_by(models.Goal.id))],
        "checked_off": [c.feature_name for c in db.scalars(
            select(models.FeatureCheckoff)
            .order_by(models.FeatureCheckoff.feature_name))],
        "dismissed": [{"kind": d.kind, "target": d.target, "reason": d.reason}
                      for d in db.scalars(select(models.Dismissal)
                                          .order_by(models.Dismissal.id))],
    }

    never_built, stale, adoption_gaps = _gap_lists(db, today)
    return {
        "repos": repo_list,
        "work": work,
        "work_note": work_note,
        "activity": _monthly(list(db.scalars(select(models.ActivityDaily))),
                             "date", ("commits", "sessions", "prompts")),
        "cost": _monthly(list(db.scalars(select(models.CostDaily))),
                         "date", ("cost_usd",)),
        "grade": grade,
        "adoption": adoption,
        "commitments": commitments,
        "history": recommendation_history(db),
        "never_built": never_built,
        "stale": stale,
        "adoption_gaps": adoption_gaps,
    }


def render_corpus_prompt(ctx: dict) -> str:
    def listing(items) -> str:
        return ", ".join(items) if items else "(none)"

    lines = ["## Repos"]
    for r in ctx["repos"]:
        lines.append(f"- {r['repo']}: {r['units']} units, {r['first']} to "
                     f"{r['last']}, mean complexity {r['mean_complexity']}, "
                     f"tags: {listing(r['tags'])}")
    if not ctx["repos"]:
        lines.append("- (nothing ingested yet)")

    lines.append("\n## Work shipped")
    if ctx["work_note"]:
        lines.append(ctx["work_note"])
    for w in ctx["work"]:
        lines.append(f"- [{w['date']}] {w['repo']} (cx {w['complexity']}, "
                     f"{listing(w['tags'])}): {w['title']} — {w['summary']}")

    lines.append("\n## Activity by month")
    for a in ctx["activity"]:
        lines.append(f"- {a['month']}: {a['commits']} commits, "
                     f"{a['sessions']} sessions, {a['prompts']} prompts")

    lines.append("\n## Spend by month")
    for c in ctx["cost"]:
        lines.append(f"- {c['month']}: ${c['cost_usd']:.2f}")

    lines.append("\n## Grade")
    g = ctx["grade"]
    if g is None:
        lines.append("- (not enough data)")
    else:
        lines.append(f"- {g['level_label']}, {g['percent_to_next']}% toward "
                     f"{g['next_label'] or 'the top'}")
        for gap in g["gaps"]:
            lines.append(f"  - gap {gap['tag']}: have {gap['have']['count']}, "
                         f"need {gap['need']['min_count']}; best fit repo: "
                         f"{gap['best_fit_repo']}")

    lines.append("\n## Claude Code adoption")
    for a in ctx["adoption"]:
        lines.append(f"- {a['feature']}: {a['status']} "
                     f"(last used {a['last_used'] or 'never'})")

    lines.append("\n## Commitments")
    for gl in ctx["commitments"]["goals"]:
        lines.append(f"- goal ({gl['status']}): {gl['title']} -> {gl['target']}")
    lines.append(f"- checked off: {listing(ctx['commitments']['checked_off'])}")
    for d in ctx["commitments"]["dismissed"]:
        lines.append(f"- dismissed {d['target']}: {d['reason'] or 'no reason given'} "
                     "(considered and waved off — do not suggest it)")

    lines.extend(_render_history(ctx["history"]))

    lines.append("\n## Allowed recommendation targets")
    lines.append(f"tags never built: {listing(ctx['never_built'])}")
    lines.append(f"tags stale over {STALE_DAYS} days: {listing(ctx['stale'])}")
    lines.append(f"Claude Code features never adopted: {listing(ctx['adoption_gaps'])}")
    return "\n".join(lines)


def _client_factory():
    """Real Anthropic client, or None when no key is configured."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def _text_of(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


ASSESSMENT_MODEL = "claude-sonnet-5"

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": ["tag", "feature"]},
                    "target": {"type": "string"},
                    "why": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["title", "kind", "target", "why", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "recommendations"],
    "additionalProperties": False,
}

ASSESSMENT_SYSTEM = (
    "You are a build coach for a solo developer's side projects. You are shown "
    "their entire build history: every unit of work with its title, prose "
    "summary and complexity, per-repo rollups, activity and spend by month, a "
    "rubric grade with per-gap best-fit repos, Claude Code adoption, and their "
    "goals, check-offs and dismissals. Write a standing assessment: a summary "
    "of where they actually are and the pattern in what they build, then 3-5 "
    "ranked recommendations. Every recommendation's `target` MUST be copied "
    "exactly from the Allowed recommendation targets section. Every "
    "`evidence` MUST cite specific work they shipped -- repos, counts, titles -- "
    "and must not restate the recommendation. A recommendation that has been "
    "made three or more times and was never acted on must either be argued "
    "on materially different grounds or dropped in favour of something else. "
    "Be direct and concrete."
)

DELTA_SYSTEM = (
    "You are a build coach for a solo developer's side projects. You are shown "
    "the current standing assessment, what has changed since it was written, "
    "and the history of every recommendation made so far. Write a short "
    "amendment: what the change means, and 0-2 recommendations. Do not restate "
    "the assessment. A recommendation that has been made three or more times "
    "and was never acted on must either be argued on materially different "
    "grounds or dropped in favour of something else. Returning zero "
    "recommendations is a correct answer when nothing warrants one. Every "
    "`target` MUST be copied exactly from the Allowed recommendation targets "
    "section."
)


def _parse(text: str) -> tuple[str, list[dict]]:
    """(summary, recommendations). Unparseable output degrades to prose.

    The shape check on `recommendations` matters as much as the JSON parse:
    `list({"a": 1})` and `list("abc")` both happily produce a list without
    raising, which would otherwise smuggle a dict or a string past this
    function and into `_store`, which expects a list of dict recommendations.
    """
    try:
        data = json.loads(text)
        summary = data["summary"]
        recs = data["recommendations"]
        if not isinstance(summary, str) or not isinstance(recs, list):
            raise TypeError("brief JSON has the wrong shape")
        return summary, recs
    except (ValueError, KeyError, TypeError):
        log.warning("brief response was not the expected JSON; storing as prose")
        return text, []


def _store(db, row: models.Brief, summary: str, recs: list[dict],
           allowed: dict[str, str], now: datetime) -> None:
    """Write the summary and its recommendations, superseding prior open rows.

    `allowed` maps target -> kind, derived from the vocabulary the model was
    given rather than trusting `rec["kind"]`: the model's `kind` and `target`
    could disagree, and outcome tracking keys off (kind, target), so a wrong
    `kind` would strand a row that supersede-by-target here would otherwise
    replace. A target outside `allowed` is dropped rather than stored: nothing
    in the UI could act on a dangling target, and its siblings are still good.

    Every recommendation is validated before any row is added, so a malformed
    entry partway through the list can never leave a half-written brief behind
    -- either every valid entry lands, or (on an unexpected exception) none of
    this function's writes are visible without a caller commit that never
    comes, because the outer generate_* catches and marks the brief failed.
    """
    row.body = summary
    valid: list[tuple[str, dict]] = []
    for rec in recs:
        if not isinstance(rec, dict):
            log.warning("dropping non-dict recommendation entry %r", rec)
            continue
        target = str(rec.get("target", ""))
        if target not in allowed:
            log.warning("dropping recommendation with unknown target %r", target)
            continue
        valid.append((target, rec))

    for ord_, (target, rec) in enumerate(valid):
        for prior in db.scalars(
                select(models.BriefRecommendation)
                .where(models.BriefRecommendation.target == target,
                       models.BriefRecommendation.outcome == "open")):
            prior.outcome = "superseded"
            prior.outcome_at = now.isoformat()
        db.add(models.BriefRecommendation(
            brief=row, ord=ord_, title=str(rec.get("title", ""))[:200],
            kind=allowed[target],
            target=target[:120], why=str(rec.get("why", "")),
            evidence=str(rec.get("evidence", ""))))


def _call(db, row: models.Brief, client_factory, system: str, prompt: str,
          params: dict, now: datetime) -> str:
    """Shared call + accounting. Returns the response text, or "" on failure."""
    client = client_factory()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; the server cannot generate briefs")
    response = client.messages.create(
        model=row.model, system=system,
        messages=[{"role": "user", "content": prompt}], **params)
    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
        "cache_read_input_tokens":
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens":
            getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    from . import usage_api
    row.input_tokens = usage["input_tokens"]
    row.output_tokens = usage["output_tokens"]
    row.cost_usd = usage_api.cost_for(row.model, usage)
    # The server's own spend is real spend; unreported it resurfaces as drift.
    usage_api.upsert_llm_daily(db, now.date().isoformat(),
                               "app-builder-coach", row.model, usage)
    # Checked after accounting so a truncated call's real spend is still
    # recorded. A truncated response is incomplete JSON, which `_parse` would
    # otherwise degrade to a silent zero-recommendation "ok" brief -- exactly
    # the failure this task exists to make visible, so it must raise instead.
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            f"{row.model} hit max_tokens; the response was truncated "
            "before it could be parsed")
    return _text_of(response)


def _new_row(kind: str, model: str, now: datetime, fp: str) -> models.Brief:
    return models.Brief(created_at=now.isoformat(), day=now.date().isoformat(),
                        kind=kind, model=model, status="ok", fingerprint=fp)


def generate_assessment(db, client_factory=_client_factory,
                        now: datetime | None = None) -> models.Brief:
    """Deep pass over the whole corpus.

    Never raises on a failed model call -- that path is caught below and
    stored as a `failed` row. `fingerprint` itself runs outside the `try` and
    is not covered by that guarantee. Caller commits.
    """
    now = now or datetime.now(timezone.utc)
    model = os.environ.get("COACH_ASSESSMENT_MODEL") or ASSESSMENT_MODEL
    row = _new_row("assessment", model, now, fingerprint(db, now.date()))
    db.add(row)
    try:
        ctx = build_corpus_context(db, now.date())
        # target -> kind, derived from the vocabulary rather than trusted from
        # the model's own `kind` field. See _store's docstring for why.
        allowed = {t: "tag" for t in ctx["never_built"]}
        allowed.update({t: "tag" for t in ctx["stale"]})
        allowed.update({f: "feature" for f in ctx["adoption_gaps"]})
        text = _call(db, row, client_factory, ASSESSMENT_SYSTEM,
                     render_corpus_prompt(ctx),
                     {"max_tokens": ASSESSMENT_MAX_TOKENS,
                      "thinking": {"type": "adaptive"},
                      "output_config": {
                          "effort": "medium",
                          "format": {"type": "json_schema",
                                     "schema": BRIEF_SCHEMA}}},
                     now)
        summary, recs = _parse(text)
        _store(db, row, summary, recs, allowed, now)
    except Exception as exc:
        log.exception("assessment generation failed")
        row.status = "failed"
        row.error = f"{type(exc).__name__}: {exc}"[:500]
        row.body = ""
    return row


MAX_DELTAS_BEFORE_REASSESS = 5


def latest_assessment(db) -> models.Brief | None:
    return db.scalar(select(models.Brief)
                     .where(models.Brief.kind == "assessment",
                            models.Brief.status == "ok")
                     .order_by(models.Brief.id.desc()).limit(1))


def decide_and_generate(db, client_factory=_client_factory,
                        now: datetime | None = None, force: bool = False) -> str:
    """Generate a brief only when there is something new to say.

    Returns "assessment", "delta" or "skipped". A "skipped" result means no
    model call was made at all -- this is the cost control, and the reason the
    page is not a wall of near-identical essays.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    fp = fingerprint(db, today)
    assessment = latest_assessment(db)
    seen = get_state(db, FP_KEY)
    try:
        deltas = int(get_state(db, DELTA_COUNT_KEY, "0"))
    except ValueError:
        deltas = 0

    def assess() -> str:
        row = generate_assessment(db, client_factory=client_factory, now=now)
        # A failed generation must NOT advance the fingerprint: the change it
        # was meant to report is still unreported, so the next ingest has to
        # retry rather than skip it forever.
        if row.status == "ok":
            set_state(db, FP_KEY, fp, now)
            set_state(db, DELTA_COUNT_KEY, "0", now)
        return "assessment"

    if force or assessment is None:
        return assess()

    # Nothing moved: this is the whole cost control, and it outranks the
    # delta counter -- a quiet stretch must not trigger a reassessment.
    if fp == seen:
        return "skipped"

    if deltas >= MAX_DELTAS_BEFORE_REASSESS:
        return assess()

    row = generate_delta(db, assessment, client_factory=client_factory, now=now)
    if row.status == "ok":
        set_state(db, FP_KEY, fp, now)
        set_state(db, DELTA_COUNT_KEY, str(deltas + 1), now)
    return "delta"


def generate_delta(db, assessment, client_factory=_client_factory,
                   now: datetime | None = None) -> models.Brief:
    """Short amendment against the standing assessment.

    Never raises on a failed model call -- that path is caught below and
    stored as a `failed` row. `fingerprint` itself runs outside the `try` and
    is not covered by that guarantee.
    """
    now = now or datetime.now(timezone.utc)
    model = os.environ.get("COACH_BRIEF_MODEL") or DEFAULT_MODEL
    row = _new_row("delta", model, now, fingerprint(db, now.date()))
    db.add(row)
    try:
        ctx = build_delta_context(db, now.date(), assessment)
        # target -> kind, derived from the vocabulary rather than trusted from
        # the model's own `kind` field. See _store's docstring for why.
        allowed = {t: "tag" for t in ctx["never_built"]}
        allowed.update({t: "tag" for t in ctx["stale"]})
        allowed.update({f: "feature" for f in ctx["adoption_gaps"]})
        # No effort, no thinking, no cache_control: all three are wrong for
        # claude-haiku-4-5. output_config.format IS supported and is required.
        text = _call(db, row, client_factory, DELTA_SYSTEM,
                     render_delta_prompt(ctx),
                     {"max_tokens": MAX_TOKENS,
                      "output_config": {"format": {"type": "json_schema",
                                                   "schema": BRIEF_SCHEMA}}},
                     now)
        summary, recs = _parse(text)
        _store(db, row, summary, recs, allowed, now)
    except Exception as exc:
        log.exception("delta generation failed")
        row.status = "failed"
        row.error = f"{type(exc).__name__}: {exc}"[:500]
        row.body = ""
    return row
