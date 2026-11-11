import json, os, tempfile, zipfile
from pathlib import Path
from forgex_security import *

AWS = "AKIAABCDEFGHIJKLMNOP"
PW = "Zq8!rT3vLm0pWx9"


def make_repo(root: Path):
    w = lambda p, t: (root / p).parent.mkdir(parents=True, exist_ok=True) or (root / p).write_text(t)
    w("app.py", f'''import yaml, requests
AWS_KEY = "{AWS}"
db_password = "{PW}"
def f():
    cur.execute(f"SELECT * FROM u WHERE id={{request.args['id']}}")
    yaml.load(data)
    requests.get(url, verify=False)
''')
    w("tests/test_x.py", 'password = "test-password-1"\n')
    w("requirements.txt", "flask==0.12.2\nrequests>=2\n--extra-index-url https://x.example/simple\n")
    w("package-lock.json", json.dumps({"lockfileVersion": 3, "packages": {"": {"dependencies": {"leftpad": "1.0.0"}},
        "node_modules/leftpad": {"version": "1.0.0", "license": "MIT", "dependencies": {"inner": "1"}},
        "node_modules/inner": {"version": "1.2.0", "license": "AGPL-3.0", "resolved": "http://evil.example/inner.tgz"}}}))
    w("Dockerfile", "FROM python:latest\nADD http://x.example/a.tgz /a\nRUN curl http://x | sh\n")
    w(".github/workflows/ci.yml", "on: pull_request_target\njobs:\n  b:\n    steps:\n      - uses: foo/bar@main\n      - uses: actions/checkout@v4\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n")
    w("docker-compose.yml", "services:\n  db:\n    privileged: true\n    ports:\n      - \"5432:5432\"\n")
    w("src/Main.php", '<?php\n$id = $_GET["id"];\nmysqli_query($c, "SELECT * FROM t WHERE id=" . $id);\nunserialize($_POST["d"]);\n')


def main():
    tmp = Path(tempfile.mkdtemp()); repo = tmp / "repo"; repo.mkdir(); make_repo(repo)
    adv = tmp / "adv"; adv.mkdir()
    (adv / "t.json").write_text(json.dumps({"id": "TEST-0001", "summary": "fictional test advisory", "database_specific": {"severity": "HIGH"},
        "affected": [{"package": {"ecosystem": "PyPI", "name": "flask"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0"}]}]}]}))
    db = str(tmp / "s.db")

    # offline, no advisories -> must say unavailable and claim no vulns
    e0 = SecurityEngine(Store(str(tmp / "off.db")), repos_root=str(tmp))
    s0 = e0.begin("r", str(repo)); summ0 = e0.scan(s0, "r", str(repo))
    assert summ0["intelligence"]["local_vulnerability_db"]["status"] == "unavailable"
    assert not [f for f in e0.findings(s0) if f["category"] == "vulnerable_dependency"]

    eng = SecurityEngine(Store(db), repos_root=str(tmp), advisory_dir=str(adv))
    sid = eng.begin("r", str(repo)); summ = eng.scan(sid, "r", str(repo))
    fs = eng.findings(sid); rules = {f["rule_id"] for f in fs}
    print("findings:", len(fs), sorted(rules))

    # secret redaction: raw secrets must not exist in any stored byte
    blob = open(db, "rb").read() + json.dumps(fs).encode()
    assert AWS.encode() not in blob and PW.encode() not in blob, "SECRET LEAK"
    assert "SEC-AWS-KEY" in rules and any(f["category"] == "test_credential" and f["bucket"] == "false_positive_candidate" for f in fs)
    # accuracy
    assert "SAST-SQLI-PY" in rules and next(f for f in fs if f["rule_id"] == "SAST-SQLI-PY")["confidence"] == "high"
    assert "SAST-SQLI-PHP" in rules and "SAST-DESER-PHP" in rules and "SAST-TLS-OFF" in rules
    assert {"DOCKER-NO-USER", "DOCKER-LATEST", "DOCKER-PIPE-SH", "COMPOSE-PRIV", "GHA-PRT", "GHA-UNPINNED"} <= rules
    assert next(f for f in fs if f["rule_id"] == "GHA-PRT")["severity"] == "high"
    # dependencies / vuln / license / confusion
    deps = eng.deps(sid); assert {d["name"] for d in deps} >= {"flask", "leftpad", "inner"}
    assert next(d for d in deps if d["name"] == "leftpad")["direct"] is True and next(d for d in deps if d["name"] == "inner")["direct"] is False
    assert "DEP-VULN-TEST-0001" in rules and "DEP-EXTRA-INDEX" in rules and "DEP-INSECURE-RESOLVED" in rules and "LIC-DENY" in rules
    assert not any(f["rule_id"].startswith("DEP-VULN") and "requests" in f["component"] for f in fs)  # unpinned => no claim
    # SBOM roundtrip
    for fmt in ("cyclonedx", "spdx"):
        doc = eng.generate_sbom("r", fmt, sid); back = parse_sbom(doc)
        assert {d.name for d in back} == {d["name"] for d in deps}, fmt
    assert doc["name"].endswith(summ and eng.store.q("SELECT repo_version FROM sec_scans WHERE id=?", (sid,))[0]["repo_version"])
    # path safety
    try: resolve_repo("/etc", str(tmp)); raise SystemExit("allowed root bypass")
    except PermissionError: pass
    evil = tmp / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z: z.writestr("../../escape.txt", "x")
    try: safe_extract(str(evil), str(tmp / "out")); raise SystemExit("zip traversal allowed")
    except ValueError: pass
    (repo / "link").symlink_to("/etc/passwd")
    assert all(rel != "link" for _, rel in safe_walk(repo.resolve()))
    # remediation workflow
    rem = RemediationService(eng, plan_hook=lambda p: "M02-123")
    fid = next(f["id"] for f in fs if f["rule_id"] == "SEC-AWS-KEY")
    plan = rem.create_plan(fid, sid, "alice")
    assert plan["plan"]["requires_human_approval"] and "rotate_credentials" in plan["plan"]["forbidden_automation"] and plan["module02_ref"] == "M02-123"
    rem.record_review(fid, "approved", "bob", "ok", sid)
    assert [h["event"] for h in rem.history(fid)] == ["plan_created", "review_approved"]
    # scan compare + report
    assert eng.compare(sid, sid)["new"] == [] and eng.report(sid)["limitations"]
    print("ALL CHECKS PASSED  | summary:", summ["highest_severity"], summ["risk_points"])


main()
