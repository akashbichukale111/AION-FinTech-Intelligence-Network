import uuid
from sqlalchemy import String, Text, Integer, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .util import utcnow

def uid(p):
    return f"{p}_{uuid.uuid4().hex[:10]}"

class Task(Base):
    __tablename__ = "eng_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("tsk"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(16))
    repo_id: Mapped[str] = mapped_column(String(128), index=True)   # Module 01 repository id
    status: Mapped[str] = mapped_column(String(24), default="new")
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # facts, evidence refs, hypotheses, impact, risk factors

class Plan(Base):
    __tablename__ = "eng_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("pln"))
    task_id: Mapped[str] = mapped_column(ForeignKey("eng_tasks.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[dict] = mapped_column(JSON)
    risk: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON)
    scan_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

class Step(Base):
    __tablename__ = "eng_plan_steps"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("eng_plans.id"), index=True)
    code: Mapped[str] = mapped_column(String(8))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))
    files: Mapped[list] = mapped_column(JSON)
    depends_on: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

class History(Base):
    __tablename__ = "eng_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    event: Mapped[str] = mapped_column(String(48))
    actor: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at = mapped_column(DateTime(timezone=True), default=utcnow)
