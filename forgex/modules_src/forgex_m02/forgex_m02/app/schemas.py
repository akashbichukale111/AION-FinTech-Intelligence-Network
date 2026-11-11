from typing import Literal, Optional
from pydantic import BaseModel, Field

TaskType = Literal["bug", "feature", "refactor", "security", "performance", "tech_debt", "fintech_reliability"]
Priority = Literal["low", "medium", "high", "critical"]

class TaskCreate(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=20, max_length=10000)
    type: TaskType
    priority: Priority = "medium"
    repo_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$")
    logs: Optional[str] = Field(default=None, max_length=20000)

class PlanCreate(BaseModel):
    supersede: bool = False

class PlanUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=5, max_length=240)
    proposed_solution: Optional[str] = Field(default=None, max_length=5000)
    notes: Optional[str] = Field(default=None, max_length=5000)

class Decision(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)

class StepUpdate(BaseModel):
    status: Literal["pending", "in_progress", "done", "blocked", "skipped"]
    note: Optional[str] = Field(default=None, max_length=2000)
