"""LLM client with three backends: offline mock, Anthropic API, Amazon Bedrock."""
import json
import os

from . import config

LABELS = ["EMERGENCY", "URGENT", "ROUTINE", "ADMIN"]


def _parse(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    out = json.loads(text[start:end + 1])
    if out.get("label") not in LABELS:
        raise ValueError(f"bad label: {out.get('label')}")
    return out


class LLMClient:
    def __init__(self, provider: str = config.LLM_PROVIDER):
        self.provider = provider

    def classify(self, prompt: dict, user_msg: str, model_id: str) -> dict:
        if self.provider == "mock":
            return self._mock(prompt, user_msg, model_id)
        if self.provider == "anthropic":
            return self._anthropic(prompt, user_msg, model_id)
        if self.provider == "bedrock":
            return self._bedrock(prompt, user_msg, model_id)
        raise ValueError(self.provider)

    # --- Offline deterministic model so CI/evals run without secrets ---------
    def _mock(self, prompt, user_msg, model_id):
        text = user_msg.lower()
        for label in LABELS:  # priority order: most urgent first
            if any(k in text for k in prompt["mock_hints"].get(label, [])):
                conf = 0.9 if "sonnet" in model_id else 0.8
                return {"label": label, "rationale": f"matched {label} criteria",
                        "confidence": conf, "tokens_in": 350, "tokens_out": 40}
        # No match: small model is unsure -> router escalates; large model defaults safe.
        if "sonnet" in model_id:
            return {"label": "URGENT", "rationale": "unclear; escalating for nurse review",
                    "confidence": 0.75, "tokens_in": 350, "tokens_out": 45}
        return {"label": "ROUTINE", "rationale": "no criteria matched",
                "confidence": 0.4, "tokens_in": 350, "tokens_out": 40}

    def _anthropic(self, prompt, user_msg, model_id):
        import anthropic
        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from K8s Secret
        resp = client.messages.create(
            model=model_id, max_tokens=200, system=prompt["system"],
            messages=[{"role": "user", "content": user_msg}],
        )
        out = _parse(resp.content[0].text)
        out.update(tokens_in=resp.usage.input_tokens, tokens_out=resp.usage.output_tokens)
        return out

    def _bedrock(self, prompt, user_msg, model_id):
        import boto3
        brt = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
        resp = brt.converse(
            modelId=model_id,  # Bedrock model/inference-profile ID from your account
            system=[{"text": prompt["system"]}],
            messages=[{"role": "user", "content": [{"text": user_msg}]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0},
        )
        out = _parse(resp["output"]["message"]["content"][0]["text"])
        out.update(tokens_in=resp["usage"]["inputTokens"], tokens_out=resp["usage"]["outputTokens"])
        return out
