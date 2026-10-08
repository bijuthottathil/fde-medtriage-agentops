from fastapi.testclient import TestClient

from app.agent import TriageAgent
from app.main import app


def test_red_flag_short_circuits_without_llm():
    out = TriageAgent("v2").run("I have chest pain", "P1002")
    assert out["label"] == "EMERGENCY"
    assert out["trace"]["models_used"] == []      # no LLM spend
    assert out["trace"]["cost_usd"] == 0


def test_high_risk_patient_never_routine_when_unsure():
    out = TriageAgent("v2").run("I just feel off today", "P1001")  # CHF patient
    assert out["label"] in ("URGENT", "EMERGENCY")


def test_admin_message():
    assert TriageAgent("v2").run("Need a refill please", "P1009")["label"] == "ADMIN"


def test_api_contract():
    c = TestClient(app)
    assert c.get("/healthz").json()["ok"]
    r = c.post("/triage", json={"patient_id": "P1003", "message": "my face is drooping"}).json()
    assert r["label"] == "EMERGENCY" and "trace" in r
