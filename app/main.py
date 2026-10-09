"""FastAPI service exposed on EKS."""
import logging
import json

from fastapi import FastAPI
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel

from . import config
from .agent import TriageAgent

log = logging.getLogger("triage")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title="MedTriage Agent")
app.mount("/metrics", make_asgi_app())

REQS = Counter("triage_requests_total", "Requests", ["label", "prompt_version", "app_version"])
ERRS = Counter("triage_errors_total", "Errors", ["app_version"])
LAT = Histogram("triage_latency_seconds", "Latency", ["app_version"])
COST = Counter("triage_llm_cost_usd_total", "LLM spend", ["model"])
ESC = Counter("triage_escalations_total", "Small->large model escalations")
CACHE_HITS = Counter("triage_llm_cache_hits_total", "LLM calls served from cache (not billed)")

agent = TriageAgent()


@app.on_event("startup")
def warm_rag():
    # uvicorn accepts traffic (and /healthz passes) only after startup finishes
    if agent.use_protocols:
        from .rag import warm
        warm()


class TriageIn(BaseModel):
    patient_id: str
    message: str


@app.get("/healthz")
def healthz():
    return {"ok": True, "app_version": config.APP_VERSION, "prompt_version": agent.prompt_version}


@app.post("/triage")
def triage(body: TriageIn):
    try:
        with LAT.labels(config.APP_VERSION).time():
            out = agent.run(body.message, body.patient_id)
    except Exception:
        ERRS.labels(config.APP_VERSION).inc()
        # Fail safe: never drop a patient message on the floor.
        return {"label": "URGENT", "queue": "nurse-callback-same-day",
                "rationale": "agent error, routed to human", "confidence": 0.0}
    t = out["trace"]
    REQS.labels(out["label"], t["prompt_version"], t["app_version"]).inc()
    if len(t["models_used"]) > 1:
        ESC.inc()
    CACHE_HITS.inc(t["cache_hits"])
    if t["models_used"]:
        COST.labels(t["models_used"][-1]).inc(t["cost_usd"])
    # Structured trace log (ship to CloudWatch/OpenSearch). No message text = no PHI in logs.
    log.info(json.dumps({"event": "triage", "label": out["label"], **{k: v for k, v in t.items() if k != "steps"},
                         "tools": [s["tool"] for s in t["steps"]]}))
    return out
