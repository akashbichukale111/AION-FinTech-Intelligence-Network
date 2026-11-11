"""Minimal, dependency-free docker-compose parser (services, image, build, depends_on)."""
from __future__ import annotations


def _val(s: str) -> str:
    return s.split(":", 1)[1].strip().strip("\"'") if ":" in s else ""


def parse_compose(lines: list[str]) -> dict[str, dict]:
    svc: dict[str, dict] = {}
    in_services, base, child, cur, sec = False, None, None, None, None
    for i, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        s = raw.strip()
        if indent == 0:
            in_services = s.startswith("services:")
            cur, base, child = None, None, None
            continue
        if not in_services:
            continue
        if base is None:
            base = indent
        if indent == base and s.endswith(":"):
            cur = s[:-1].strip().strip("\"'")
            svc[cur] = {"line": i, "image": None, "build": None, "depends_on": [], "ports": []}
            sec, child = None, None
            continue
        if cur is None:
            continue
        if child is None and indent > base:
            child = indent
        if indent == child:
            sec = None
            key = s.split(":", 1)[0].strip()
            v = _val(s)
            if key == "image":
                svc[cur]["image"] = v
            elif key == "build":
                if v:
                    svc[cur]["build"] = v
                else:
                    sec = "build"
            elif key in ("depends_on", "ports"):
                if v.startswith("["):
                    items = [x.strip().strip("\"'") for x in v.strip("[]").split(",") if x.strip()]
                    svc[cur][key].extend(items)
                else:
                    sec = key
        elif indent > (child or base):
            if sec == "build" and s.startswith("context:"):
                svc[cur]["build"] = _val(s)
            elif sec == "depends_on":
                if s.startswith("-"):
                    svc[cur]["depends_on"].append(s[1:].strip().strip("\"'"))
                elif s.endswith(":"):
                    svc[cur]["depends_on"].append(s[:-1].strip())
            elif sec == "ports" and s.startswith("-"):
                svc[cur]["ports"].append(s[1:].strip().strip("\"'"))
    return svc
