"""Essential checks: isolation, execution safety, regression detection, financial correctness, honest gating."""
import os, socket, textwrap, threading, time
from decimal import Decimal
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGEX_TESTING_DB", str(tmp_path / "t.db"))

from forgex.testing import fintech, quality_gate, runner, service, store

PAY_V1 = textwrap.dedent('''
    from decimal import Decimal
    def apply_discount(amount: Decimal, pct: Decimal) -> Decimal:
        if pct < 0 or pct > 100:
            raise ValueError("bad pct")
        return (amount * (Decimal(100) - pct) / Decimal(100)).quantize(Decimal("0.01"))
''')
PAY_V2 = PAY_V1.replace("Decimal(100) - pct", "Decimal(99) - pct")      # silent behaviour change
TEST_PAY = textwrap.dedent('''
    from decimal import Decimal
    from pay import apply_discount
    def test_ten_percent():
        assert apply_discount(Decimal("100.00"), Decimal("10")) == Decimal("90.00")
    def test_rejects_bad_pct():
        import pytest
        with pytest.raises(ValueError):
            apply_discount(Decimal("1"), Decimal("101"))
''')

def make_repo(tmp, files):
    root = Path(tmp) / "repo"; root.mkdir(exist_ok=True)
    for n, c in files.items():
        (root / n).parent.mkdir(parents=True, exist_ok=True); (root / n).write_text(c)
    return str(root)

def run(repo, **kw):
    r = service.create_run({"repo_path": repo, "authorized": True, "timeout_seconds": kw.pop("timeout_seconds", 60), "allow_unisolated_network": True, **kw})
    for _ in range(600):
        cur = store.get("test_run", r["id"])
        if cur["status"] not in ("queued", "running"): return cur
        time.sleep(0.1)
    raise AssertionError("run did not finish")

def test_unauthorized_run_is_refused(tmp_path):
    with pytest.raises(PermissionError):
        service.create_run({"repo_path": make_repo(tmp_path, {"pay.py": PAY_V1}), "authorized": False})

def test_isolation_host_repo_untouched_env_scrubbed_network_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGEX_SECRET_DEMO", "topsecretvalue123")
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5); port = srv.getsockname()[1]
    net = runner.detect_isolation()["netns"] or runner.detect_isolation()["docker"]
    body = textwrap.dedent(f'''
        import os, socket, pathlib
        def test_writes_stay_in_workspace():
            pathlib.Path("created_by_test.txt").write_text("x")
            print("leak-check topsecretvalue123")
        def test_env_is_scrubbed():
            assert "FORGEX_SECRET_DEMO" not in os.environ
        def test_host_loopback_unreachable():
            s = socket.socket(); s.settimeout(2)
            try: s.connect(("127.0.0.1", {port})); reached = True
            except OSError: reached = False
            assert reached is False
    ''')
    repo = make_repo(tmp_path, {"test_iso.py": body})
    r = run(repo)
    assert not (Path(repo) / "created_by_test.txt").exists(), "test wrote into the host repo"
    if net: assert r["summary"]["failed"] == 0 and r["status"] == "passed", r["results"]
    else: pytest.skip("no network isolation on this host; env/fs checks below still apply")
    logs = store.get("run_logs", r["id"])
    assert "topsecretvalue123" not in str(logs), "secret leaked into stored logs"

def test_env_and_fs_isolation_even_without_netns(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGEX_SECRET_DEMO", "topsecretvalue123")
    repo = make_repo(tmp_path, {"test_a.py": "import os, pathlib\ndef test_a():\n    assert 'FORGEX_SECRET_DEMO' not in os.environ\n    pathlib.Path('x.txt').write_text('1')\n    print('topsecretvalue123')\n    assert 0, 'topsecretvalue123'\n"})
    r = run(repo)
    assert not (Path(repo) / "x.txt").exists()
    assert "topsecretvalue123" not in str(r["results"]) and "topsecretvalue123" not in str(store.get("run_logs", r["id"]))

def test_timeout_is_not_success_and_gate_fails(tmp_path):
    repo = make_repo(tmp_path, {"test_hang.py": "def test_hang():\n    while True: pass\n"})
    t0 = time.time(); r = run(repo, timeout_seconds=3)
    assert time.time() - t0 < 30
    assert r["status"] == "timeout" and r["quality_gate"]["verdict"] == "fail"

def test_zero_tests_never_pass(tmp_path):
    repo = make_repo(tmp_path, {"pay.py": PAY_V1, "tests/__init__.py": ""})
    r = run(repo, command="python -m pytest -q")
    assert r["status"] in ("incomplete", "error") and r["quality_gate"]["verdict"] in ("incomplete", "fail")

def test_import_error_is_infrastructure_not_assertion(tmp_path):
    repo = make_repo(tmp_path, {"test_x.py": "import not_a_real_dependency_xyz\ndef test_x(): pass\n"})
    r = run(repo)
    assert r["status"] in ("failed", "error")
    assert any(g["category"] == "import" and g["nature"] == "possible_infrastructure" for g in r["failure_groups"])

def test_regression_detected_with_evidence_and_gate_fails(tmp_path):
    repo = make_repo(tmp_path, {"pay.py": PAY_V1, "test_pay.py": TEST_PAY})
    base = run(repo, include_fintech=True)
    assert base["status"] == "passed" and base["quality_gate"]["verdict"] in ("pass", "conditional")
    assert base["coverage"] is None or base["coverage"]["total"]["line_pct"] is not None
    (Path(repo) / "pay.py").write_text(PAY_V2)
    cur = run(repo, base_run_id=base["id"], changes=[{"path": "pay.py", "change_type": "modified", "before": PAY_V1, "after": PAY_V2}])
    reg = cur["regression"]
    assert cur["status"] == "failed" and "test_pay::test_ten_percent" in reg["newly_failing"]
    assert any(a["path"] == "test_pay.py" for a in reg["affected_tests"])
    g = cur["failure_groups"][0]
    assert g["category"] == "assertion" and "pay.py" in g["likely_cause"] and g["confidence"] in ("medium", "high")
    assert cur["quality_gate"]["verdict"] == "fail" and "regressions" in cur["quality_gate"]["critical_failures"]
    cmp = service.compare_runs(base, cur)
    assert "test_pay::test_ten_percent" in cmp["new_failures"]

def test_generated_regression_test_catches_behaviour_change(tmp_path):
    repo = make_repo(tmp_path, {"pay.py": PAY_V2, "test_pay.py": TEST_PAY.replace("90.00", "89.90")})
    out = service.generate_tests({"repo_path": repo, "changes": [{"path": "pay.py", "change_type": "modified", "before": PAY_V1, "after": PAY_V2}]})
    p = out["proposals"][0]
    assert p["validation"]["ok"] and "regression" in p["kinds"] and not (Path(repo) / p["test_path"]).exists()
    service.review_proposal(p["id"], "approve", "tester")
    r = run(repo, command="python -m pytest -q --junitxml=junit.xml")
    names = {x["name"]: x["status"] for x in r["results"]}
    assert any("regression_against_previous" in n and s == "failed" for n, s in names.items()), names
    assert any("edge_inputs" in n and s == "passed" for n, s in names.items()), names

def test_generated_code_validation_blocks_unsafe_code():
    from forgex.testing.generation import validate
    assert not validate("import subprocess\ndef test_x():\n    subprocess.run(['rm','-rf','/'])\n")["ok"]

def test_fintech_deterministic_and_detects_defects():
    a, b = fintech.run_simulations("reference"), fintech.run_simulations("reference")
    assert a == b and a["summary"]["failed"] == 0
    assert fintech.run_simulations("defective")["summary"]["failed"] >= 5
    with pytest.raises(TypeError): fintech.money(0.1)
    assert fintech.money("0.10") + fintech.money("0.20") == Decimal("0.30")

def test_critical_failure_not_hidden_by_score():
    results = [{"name": f"t{i}", "status": "passed"} for i in range(99)] + [{"name": "test_refund_critical", "status": "failed"}]
    run_ = {"status": "failed", "exit_code": 1, "results": results, "summary": {"total": 100, "passed": 99, "failed": 1, "errors": 0, "skipped": 0}}
    g = quality_gate.evaluate(run_, [], None, None, None, critical_tests=["refund_critical"])
    assert g["verdict"] == "fail" and "critical_tests" in g["critical_failures"] and g["score"] <= 49

def test_missing_coverage_is_unknown_not_invented(tmp_path):
    repo = make_repo(tmp_path, {"pay.py": PAY_V1, "test_pay.py": TEST_PAY})
    r = run(repo, collect_coverage=False)
    assert r["coverage"] is None and "coverage" in r["quality_gate"]["unknowns"] and r["quality_gate"]["verdict"] == "conditional"
