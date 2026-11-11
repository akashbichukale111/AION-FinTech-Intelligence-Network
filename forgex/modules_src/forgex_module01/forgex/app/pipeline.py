from __future__ import annotations

import time
from pathlib import Path

from . import architecture, dependencies, detect, health, report, scanner
from .context import Ctx
from .evidence import Registry


class AnalysisError(ValueError):
    pass


def run(root: Path, name: str) -> dict:
    t0 = time.time()
    files, meta = scanner.scan(root)
    if not files:
        raise AnalysisError("No analysable files found (empty directory or everything ignored).")
    if not any(f.category in ("source", "config", "infra") for f in files):
        raise AnalysisError("Directory does not look like a software repository (no source, config or infra files).")
    ctx = Ctx(root, files, Registry())
    deps = dependencies.analyze(ctx)
    stack = detect.detect(ctx, deps)
    arch = architecture.analyze(ctx, deps, stack)
    hl = health.analyze(ctx, deps, arch)
    tree = scanner.build_tree(files, name)
    return report.assemble(name, files, meta, stack, deps, arch, hl, ctx.reg, tree, time.time() - t0)
