import re, pathlib
from decimal import Decimal
from fastapi.testclient import TestClient
from forgex_m06.api import create_app, set_lab
from forgex_m06.service import Lab

B = "/api/v1/fintech-lab"

def client():
    set_lab(Lab(":memory:")); return TestClient(create_app())

def test_all_scenarios_run_and_are_deterministic():
    c = client()
    for s in c.get(f"{B}/scenarios").json()["items"]:
        a = c.post(f"{B}/runs", json={"scenario_id": s["scenario_id"]}); assert a.status_code == 201, a.text
        b = c.post(f"{B}/runs", json={"scenario_id": s["scenario_id"]}).json()
        assert a.json()["digest"] == b["digest"], s["name"]

def test_findings_traces_report_and_task():
    c = client()
    sid = c.get(f"{B}/scenarios").json()["items"][0]["scenario_id"]
    r = c.post(f"{B}/runs", json={"scenario_id": sid}).json()["run_id"]
    assert c.get(f"{B}/runs/{r}/traces").status_code == 200
    assert c.get(f"{B}/reliability-report/{r}").status_code == 200
    fs = c.get(f"{B}/runs/{r}/findings").json()
    assert fs

def test_no_float_money():
    for p in pathlib.Path("forgex_m06").glob("*.py"):
        assert not re.search(r"\bfloat\(", p.read_text()), p
