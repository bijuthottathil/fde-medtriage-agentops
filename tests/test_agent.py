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


def test_cache_hit_is_not_billed():
    agent = TriageAgent("v2")
    msg = "cache test: mild knee ache after a long walk"
    first = agent.run(msg, "P1006")["trace"]
    second = agent.run(msg, "P1006")["trace"]
    assert first["cost_usd"] > 0 and first["cache_hits"] == 0
    assert second["cost_usd"] == 0 and second["cache_hits"] == len(second["models_used"])


def test_rag_retrieves_matching_protocol():
    from app.rag import retrieve_protocols
    top = retrieve_protocols("my dad is confused and his left arm feels weird")[0]
    assert top["id"] == "P-STROKE" and top["label"] == "EMERGENCY"


def test_v3_uses_protocols_and_v2_does_not():
    msg = "my left calf is swollen and sore since my flight"
    v3_tools = [s["tool"] for s in TriageAgent("v3").run(msg, "P1004")["trace"]["steps"]]
    v2_tools = [s["tool"] for s in TriageAgent("v2").run(msg, "P1004")["trace"]["steps"]]
    assert "retrieve_protocols" in v3_tools and "retrieve_protocols" not in v2_tools
