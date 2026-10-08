"""Patient-message triage agent.

Plan:  red_flag_scan -> lookup_patient -> classify (small model, escalate to large)
       -> clinical_policy -> route_to_queue
Every step is recorded in a trace (AgentOps observability).
"""
import hashlib
import json
import time
from functools import lru_cache
from pathlib import Path

from . import config
from .llm import LLMClient

PATIENTS = json.loads((config.ROOT / "data" / "patients.json").read_text())

# Deterministic safety guardrail: runs BEFORE the LLM, can't be "prompted away".
HARD_RED_FLAGS = ["chest pain", "can't breathe", "cannot breathe", "slurred",
                  "overdose", "suicide", "vomiting blood", "unconscious"]

QUEUES = {
    "EMERGENCY": "call-911-script + on-call physician page",
    "URGENT": "nurse-callback-same-day",
    "ROUTINE": "scheduling-queue",
    "ADMIN": "front-desk-queue",
}
RANK = {"ADMIN": 0, "ROUTINE": 1, "URGENT": 2, "EMERGENCY": 3}


# ---- Tools ------------------------------------------------------------------
def red_flag_scan(message: str) -> list[str]:
    m = message.lower()
    return [f for f in HARD_RED_FLAGS if f in m]


def lookup_patient(patient_id: str) -> dict:
    return PATIENTS.get(patient_id, {"age": None, "conditions": [], "high_risk": False})


def route_to_queue(label: str) -> str:
    return QUEUES[label]


def cost_usd(model_id: str, tin: int, tout: int) -> float:
    pin, pout = config.PRICING.get(model_id, (0.0, 0.0))
    return round((tin * pin + tout * pout) / 1_000_000, 6)


# ---- Cached LLM call (cost optimization: identical messages aren't re-billed)
@lru_cache(maxsize=2048)
def _cached_classify(provider: str, prompt_ver: str, model_id: str, msg_hash: str, user_msg: str):
    prompt = config.load_prompt(prompt_ver)
    return json.dumps(LLMClient(provider).classify(prompt, user_msg, model_id))


class TriageAgent:
    def __init__(self, prompt_version: str | None = None, provider: str | None = None):
        self.prompt_version = prompt_version or config.prompt_version()
        self.provider = provider or config.LLM_PROVIDER

    def _step(self, trace, name, fn, *args):
        t0 = time.perf_counter()
        out = fn(*args)
        trace["steps"].append({"tool": name, "ms": round((time.perf_counter() - t0) * 1000, 2),
                               "output": out})
        return out

    def _classify(self, model_id, ctx_msg):
        h = hashlib.sha256(ctx_msg.encode()).hexdigest()
        return json.loads(_cached_classify(self.provider, self.prompt_version, model_id, h, ctx_msg))

    def run(self, message: str, patient_id: str) -> dict:
        trace = {"prompt_version": self.prompt_version, "app_version": config.APP_VERSION,
                 "steps": [], "models_used": [], "cost_usd": 0.0}
        t0 = time.perf_counter()

        flags = self._step(trace, "red_flag_scan", red_flag_scan, message)
        if flags:  # short-circuit: safer AND cheaper (no LLM call)
            label, rationale, conf = "EMERGENCY", f"hard red flag: {flags}", 1.0
        else:
            patient = self._step(trace, "lookup_patient", lookup_patient, patient_id)
            ctx_msg = (f"Patient age {patient['age']}, conditions {patient['conditions']}.\n"
                       f"Message: {message}")

            # Model routing: small model first, escalate when unsure.
            res = self._classify(config.SMALL_MODEL, ctx_msg)
            trace["models_used"].append(config.SMALL_MODEL)
            trace["cost_usd"] += cost_usd(config.SMALL_MODEL, res["tokens_in"], res["tokens_out"])
            if res["confidence"] < config.ESCALATE_BELOW_CONFIDENCE:
                res = self._classify(config.LARGE_MODEL, ctx_msg)
                trace["models_used"].append(config.LARGE_MODEL)
                trace["cost_usd"] += cost_usd(config.LARGE_MODEL, res["tokens_in"], res["tokens_out"])
            trace["steps"].append({"tool": "classify", "output": res})
            label, rationale, conf = res["label"], res["rationale"], res["confidence"]

            # Clinical policy: high-risk patients never land in ROUTINE for clinical complaints.
            if patient["high_risk"] and label == "ROUTINE" and conf < 0.85:
                label, rationale = "URGENT", rationale + " | bumped: high-risk patient"
                trace["steps"].append({"tool": "clinical_policy", "output": "bumped to URGENT"})

        queue = self._step(trace, "route_to_queue", route_to_queue, label)
        trace["cost_usd"] = round(trace["cost_usd"], 6)
        trace["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return {"label": label, "queue": queue, "rationale": rationale,
                "confidence": conf, "trace": trace}
