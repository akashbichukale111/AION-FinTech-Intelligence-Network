"""Safe, read-only file scanner. Never follows symlinks, never executes anything."""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import config

LANG_BY_EXT = {
    ".py": "Python", ".java": "Java", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".php": "PHP", ".go": "Go", ".rb": "Ruby", ".rs": "Rust", ".cs": "C#", ".kt": "Kotlin",
    ".swift": "Swift", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".scala": "Scala",
    ".sh": "Shell", ".sql": "SQL", ".vue": "Vue", ".html": "HTML", ".css": "CSS", ".scss": "CSS",
}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".pdf", ".zip", ".gz", ".tar",
    ".jar", ".war", ".class", ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2", ".ttf",
    ".eot", ".mp3", ".mp4", ".mov", ".pyc", ".bin", ".db", ".sqlite", ".map",
}
CONFIG_EXT = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".properties", ".xml", ".gradle",
    ".kts", ".lock", ".env", ".prisma", ".conf",
}
CONFIG_NAMES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "pyproject.toml",
    "poetry.lock", "pipfile", "pipfile.lock", "setup.cfg", "tox.ini", "pom.xml", "go.mod",
    "go.sum", "composer.json", "composer.lock", "gemfile", "gemfile.lock", "makefile",
    "tsconfig.json", ".gitignore", ".dockerignore", ".editorconfig", ".env", "uv.lock",
    "npm-shrinkwrap.json", "pnpm-workspace.yaml", "lerna.json", "go.work",
}
INFRA_NAMES = {"jenkinsfile", ".gitlab-ci.yml", "azure-pipelines.yml", "chart.yaml", "procfile",
               "vercel.json", "netlify.toml", "serverless.yml", "skaffold.yaml"}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "testing"}
TEST_NAME = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|test\.php$)")


@dataclass
class FileInfo:
    path: str
    size: int
    language: str | None
    category: str  # source|test|config|docs|infra|asset|other
    lines: int
    sha: str | None

    def brief(self) -> dict:
        return {"path": self.path, "size": self.size, "language": self.language,
                "category": self.category, "lines": self.lines}


def classify(rel: str) -> tuple[str | None, str]:
    name = posixpath.basename(rel)
    lname = name.lower()
    low = rel.lower()
    parts = low.split("/")
    ext = posixpath.splitext(lname)[1]
    lang = LANG_BY_EXT.get(ext)
    if (lname in INFRA_NAMES or lname.startswith(("dockerfile", "docker-compose", "compose."))
            or ext == ".tf" or ".github/workflows/" in low or ".circleci/" in low
            or any(p in {"k8s", "kubernetes", "helm", "terraform", "ansible"} for p in parts[:-1])):
        return lang, "infra"
    if lang and (any(p in TEST_DIRS for p in parts[:-1]) or TEST_NAME.search(lname)
                 or re.search(r"Tests?\.java$", name)):
        return lang, "test"
    if (lname in CONFIG_NAMES or re.fullmatch(r"requirements[\w.-]*\.txt|constraints\.txt", lname)
            or lname.startswith(".env")):
        return lang, "config"
    if ext in {".md", ".rst", ".adoc"} or parts[0] in {"docs", "doc"} or \
            lname in {"license", "readme", "changelog", "contributing", "notice"}:
        return lang, "docs"
    if lang:
        return lang, "source"
    if ext in BINARY_EXT:
        return None, "asset"
    if ext in CONFIG_EXT:
        return None, "config"
    return None, "other"


def _count_lines_stream(path: Path) -> int:
    n, last = 0, b""
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            n += chunk.count(b"\n")
            last = chunk[-1:]
    return n + (1 if last and last != b"\n" else 0)


def scan(root: Path) -> tuple[list[FileInfo], dict]:
    files: list[FileInfo] = []
    skipped: Counter = Counter()
    ignored_dirs: Counter = Counter()
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        keep = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            if d in config.IGNORE_DIRS or d.endswith(".egg-info"):
                ignored_dirs[d] += 1
            elif os.path.islink(full):
                skipped["symlinked_dir"] += 1
            else:
                keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            try:
                st = os.lstat(full)
            except OSError:
                skipped["unreadable"] += 1
                continue
            if stat.S_ISLNK(st.st_mode):
                skipped["symlink"] += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if len(files) >= config.MAX_FILES:
                truncated = True
                break
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            lang, cat = classify(rel)
            lines, sha = 0, None
            if cat != "asset":
                try:
                    if st.st_size <= config.MAX_READ_BYTES:
                        with open(full, "rb") as fh:
                            data = fh.read()
                        if b"\0" in data[:8000]:
                            cat, lang = "asset", None
                        else:
                            lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
                            sha = hashlib.sha256(data).hexdigest() if data else None
                    else:
                        lines = _count_lines_stream(Path(full))
                except OSError:
                    skipped["unreadable"] += 1
                    continue
            files.append(FileInfo(rel, st.st_size, lang, cat, lines, sha))
        if truncated:
            dirnames[:] = []
            break
    return files, {"skipped": dict(skipped), "ignored_dirs": dict(ignored_dirs), "truncated": truncated}


def build_tree(files: list[FileInfo], name: str, max_nodes: int = 4000) -> dict:
    root: dict = {"name": name, "type": "dir", "_d": {}, "_f": []}
    for f in files:
        node = root
        parts = f.path.split("/")
        for p in parts[:-1]:
            node = node["_d"].setdefault(p, {"name": p, "type": "dir", "_d": {}, "_f": []})
        node["_f"].append(f)
    budget = [max_nodes]
    truncated = [False]

    def conv(n: dict) -> dict:
        count = len(n["_f"]) + sum(0 for _ in n["_d"])
        kids = []
        total = len(n["_f"])
        for d in sorted(n["_d"].values(), key=lambda x: x["name"]):
            c = conv(d)
            total += c["files"]
            kids.append(c)
        for f in sorted(n["_f"], key=lambda x: x.path):
            if budget[0] <= 0:
                truncated[0] = True
                break
            budget[0] -= 1
            kids.append({"name": posixpath.basename(f.path), "type": "file",
                         "category": f.category, "lines": f.lines})
        return {"name": n["name"], "type": "dir", "files": total, "children": kids}

    tree = conv(root)
    tree["truncated"] = truncated[0]
    return tree
