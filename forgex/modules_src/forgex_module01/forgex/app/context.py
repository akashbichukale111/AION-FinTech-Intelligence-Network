from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .evidence import Registry
from .scanner import FileInfo


@dataclass
class Ctx:
    root: Path
    files: list[FileInfo]
    reg: Registry
    by_path: dict[str, FileInfo] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_path = {f.path: f for f in self.files}

    def lines(self, rel: str) -> list[str]:
        f = self.by_path.get(rel)
        if f is None or f.category == "asset" or f.size > config.MAX_READ_BYTES:
            return []
        try:
            return (self.root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

    def text(self, rel: str) -> str:
        return "\n".join(self.lines(rel))
