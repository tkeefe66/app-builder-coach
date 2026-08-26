"""Ingested tables (sweep-owned). App-owned tables arrive in Phase 5."""
from datetime import datetime

from sqlalchemy import (JSON, DateTime, Float, ForeignKey, Integer, String,
                        Text, func)
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column,
                            relationship)


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    captured_at: Mapped[str] = mapped_column(String(40))
    sweep_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class FeatureUnit(Base):
    __tablename__ = "feature_units"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    repo: Mapped[str] = mapped_column(String(200), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    complexity: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")


class ActivityDaily(Base):
    __tablename__ = "activity_daily"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    commits: Mapped[int] = mapped_column(Integer, default=0)
    by_repo: Mapped[dict] = mapped_column(JSON, default=dict)
    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompts: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CostDaily(Base):
    __tablename__ = "cost_daily"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    by_model: Mapped[dict] = mapped_column(JSON, default=dict)


class InfraUsage(Base):
    """Railway period-to-date dollars per app, as captured.

    Cumulative, not daily: the Railway CLI reports billing-period-to-date only.
    Daily deltas are derived at read time so re-shipping a capture is idempotent.
    """
    __tablename__ = "infra_usage"
    period_start: Mapped[str] = mapped_column(String(10), primary_key=True)
    app: Mapped[str] = mapped_column(String(64), primary_key=True)
    capture_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    cumulative_usd: Mapped[float] = mapped_column(Float, default=0.0)


class InfraServiceUsage(Base):
    """Per-service Railway cost within a project, as captured.

    Cumulative period-to-date like InfraUsage, one grouping level finer. The PK
    uses service_id rather than service_name because Railway ids are stable and
    names are user-editable.
    """
    __tablename__ = "infra_service_usage"
    period_start: Mapped[str] = mapped_column(String(10), primary_key=True)
    app: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    capture_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    service_name: Mapped[str] = mapped_column(String(120), default="")
    cumulative_usd: Mapped[float] = mapped_column(Float, default=0.0)
    memory_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_usd: Mapped[float] = mapped_column(Float, default=0.0)
    egress_usd: Mapped[float] = mapped_column(Float, default=0.0)
    volume_usd: Mapped[float] = mapped_column(Float, default=0.0)
    backup_usd: Mapped[float] = mapped_column(Float, default=0.0)


class LlmDaily(Base):
    """Per-app, per-model daily Anthropic usage, aggregated on write."""
    __tablename__ = "llm_daily"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    app: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    call_count: Mapped[int] = mapped_column(Integer, default=0)


class AdoptionHistory(Base):
    __tablename__ = "adoption_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"), index=True)
    feature_name: Mapped[str] = mapped_column(String(120), index=True)
    lesson: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(30))
    last_used: Mapped[str | None] = mapped_column(String(10), nullable=True)


class FeatureCatalog(Base):
    __tablename__ = "feature_catalog"
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    lesson: Mapped[str] = mapped_column(String(60), default="")
    source: Mapped[str] = mapped_column(String(20), default="checklist")
    discovered_at: Mapped[str] = mapped_column(String(10))


class Brief(Base):
    """One generated coaching brief, including failures.

    Failed generations are stored rather than dropped: the UI shows the last
    successful brief flagged stale, and a run of `failed` rows is the only
    visible signal that the key or the model is misconfigured.
    """
    __tablename__ = "briefs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[str] = mapped_column(String(32), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str] = mapped_column(String(500), default="")
    kind: Mapped[str] = mapped_column(String(16), default="delta", index=True)
    # NOT unique: a second delta in one day is legitimate when the change gate
    # fires twice. Uniqueness here would wrongly block it.
    day: Mapped[str] = mapped_column(String(10), default="", index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    recommendations: Mapped[list["BriefRecommendation"]] = relationship(
        back_populates="brief", cascade="all, delete-orphan")


class BriefRecommendation(Base):
    """One concrete recommendation from a brief, tracked to an outcome.

    A table rather than a JSON blob on `briefs`: the recurring rollup is a
    GROUP BY target, and converting one to a goal needs a stable row to mark.
    """
    __tablename__ = "brief_recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    ord: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(120), index=True)
    why: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(16), default="open", index=True)
    outcome_at: Mapped[str] = mapped_column(String(32), default="")
    brief: Mapped["Brief"] = relationship(back_populates="recommendations")


class WatcherState(Base):
    """Small key/value store for background-watcher bookkeeping.

    Four keys today: changelog.version_watermark, changelog.last_checked_at,
    brief.fingerprint and brief.deltas_since_assessment. A KV table rather
    than columns bolted onto an unrelated table, so a second watcher needs no
    migration.
    """
    __tablename__ = "watcher_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[str] = mapped_column(String(32), default="")


# --- App-owned tables (UI-written). Ingest must never touch these. ---

class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))          # tag | feature
    target: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    target_date: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[str] = mapped_column(String(32))


class FeatureCheckoff(Base):
    """Manual "I learned this", independent of what the sweep can detect."""
    __tablename__ = "feature_checkoffs"
    feature_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    checked_at: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(500), default="")


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(16), index=True)
    subject_id: Mapped[str] = mapped_column(String(120), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))


class Dismissal(Base):
    """A gap waved off. Filtered out of the brief, still visible on Overview."""
    __tablename__ = "dismissals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    target: Mapped[str] = mapped_column(String(120), index=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[str] = mapped_column(String(32))
