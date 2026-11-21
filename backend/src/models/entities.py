from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base

def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(250))
class Node(Base):
    __tablename__ = 'nodes'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    term: Mapped[int] = mapped_column(Integer)
    log_position: Mapped[int] = mapped_column(Integer)
    available: Mapped[bool] = mapped_column(Boolean)
    fenced: Mapped[bool] = mapped_column(Boolean)
    vote_enabled: Mapped[bool] = mapped_column(Boolean)
class Scenario(Base):
    __tablename__ = 'scenarios'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    fail_primary: Mapped[bool] = mapped_column(Boolean)
    fence_primary: Mapped[bool] = mapped_column(Boolean)
    max_data_loss: Mapped[int] = mapped_column(Integer)
    required_term: Mapped[int] = mapped_column(Integer)
class Drill(Base):
    __tablename__ = 'drills'
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey('scenarios.id'))
    label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default='queued')
    snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class Audit(Base):
    __tablename__ = 'audits'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

# 换钥演练：密钥环、数据页、备库、可恢复的改页批次与切主证据。
class KeyRecord(Base):
    __tablename__ = 'rekey_keys'
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='active')
    retain_for_recovery: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class RekeyRun(Base):
    __tablename__ = 'rekey_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    old_key_id: Mapped[int] = mapped_column(ForeignKey('rekey_keys.id'))
    new_key_id: Mapped[int] = mapped_column(ForeignKey('rekey_keys.id'))
    batch_size: Mapped[int] = mapped_column(Integer, default=4)
    tick: Mapped[int] = mapped_column(Integer, default=0)
    retire_at_tick: Mapped[int] = mapped_column(Integer, default=6)
    status: Mapped[str] = mapped_column(String(24), default='rewriting')
    retired_at_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class DataPage(Base):
    __tablename__ = 'rekey_pages'
    __table_args__ = (UniqueConstraint('run_id', 'page_no', name='uq_rekey_page_no'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('rekey_runs.id'))
    page_no: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(String(500))
    key_id: Mapped[int] = mapped_column(ForeignKey('rekey_keys.id'))
    version: Mapped[int] = mapped_column(Integer, default=1)
    rewrite_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class Replica(Base):
    __tablename__ = 'rekey_replicas'
    __table_args__ = (UniqueConstraint('run_id', 'name', name='uq_rekey_replica_name'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('rekey_runs.id'))
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20))
    term: Mapped[int] = mapped_column(Integer)
    log_position: Mapped[int] = mapped_column(Integer)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    fenced: Mapped[bool] = mapped_column(Boolean, default=False)
    vote_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    new_key_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    old_key_available: Mapped[bool] = mapped_column(Boolean, default=True)
    applied_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class RewriteBatch(Base):
    __tablename__ = 'rekey_batches'
    __table_args__ = (UniqueConstraint('run_id', 'batch_no', name='uq_rekey_batch_no'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('rekey_runs.id'))
    batch_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
class RewriteItem(Base):
    __tablename__ = 'rekey_batch_items'
    __table_args__ = (UniqueConstraint('batch_id', 'page_id', name='uq_rekey_batch_page'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('rekey_batches.id'))
    page_id: Mapped[int] = mapped_column(ForeignKey('rekey_pages.id'))
    expected_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='pending')
class FailoverRun(Base):
    __tablename__ = 'rekey_failovers'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('rekey_runs.id'))
    snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
