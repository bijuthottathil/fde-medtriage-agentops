"""Runtime config. Everything that changes behaviour is versioned and comes from env/ConfigMap."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

ENV = os.getenv("APP_ENV", "dev")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")          # mock | anthropic | bedrock

# Model versioning: two tiers for cost-optimized routing.
SMALL_MODEL = os.getenv("SMALL_MODEL_ID", "claude-haiku-5-5")
LARGE_MODEL = os.getenv("LARGE_MODEL_ID", "claude-sonnet-5-5")
ESCALATE_BELOW_CONFIDENCE = float(os.getenv("ESCALATE_BELOW_CONFIDENCE", "0.7"))

# USD per 1M tokens (input, output) — illustrative, set from your contract/pricing page.
PRICING = {
    SMALL_MODEL: (float(os.getenv("SMALL_IN_PRICE", "1.0")), float(os.getenv("SMALL_OUT_PRICE", "5.0"))),
    LARGE_MODEL: (float(os.getenv("LARGE_IN_PRICE", "3.0")), float(os.getenv("LARGE_OUT_PRICE", "15.0"))),
}

APP_VERSION = os.getenv("APP_VERSION", "local")             # set to git SHA by CI

# RAG over clinic protocols (used by prompt versions with use_protocols: true).
PROTOCOLS_PATH = ROOT / "data" / "protocols" / "protocols.yaml"
CHROMA_PATH = os.getenv("CHROMA_PATH")                      # unset = in-memory index
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
RAG_THREADS = int(os.getenv("RAG_THREADS", "1"))          # ONNX threads per worker


def prompt_version() -> str:
    override = os.getenv("PROMPT_VERSION")
    if override:
        return override
    reg = yaml.safe_load((ROOT / "prompts" / "registry.yaml").read_text())
    return reg["triage"][ENV]


def load_prompt(version: str | None = None) -> dict:
    version = version or prompt_version()
    return yaml.safe_load((ROOT / "prompts" / "triage" / f"{version}.yaml").read_text())
