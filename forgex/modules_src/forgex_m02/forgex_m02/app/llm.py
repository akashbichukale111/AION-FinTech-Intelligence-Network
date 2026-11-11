"""Optional LLM provider interface. Baseline planning is fully deterministic; no provider/API key is needed.
AI output is advisory only: it is sanitised, tagged unverified, and can never approve, execute or alter facts."""
import os
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    name = "base"
    @abstractmethod
    def propose_hypotheses(self, task: dict, facts: list[dict]) -> list[dict]:
        """Return [{title, explanation, evidence_ids:[...], alternatives:[...], investigation:[...]}] using ONLY supplied facts."""

class NullProvider(LLMProvider):
    name = "none"
    def propose_hypotheses(self, task, facts):
        return []

PROVIDERS: dict[str, type[LLMProvider]] = {"none": NullProvider}  # register future providers here

def get_provider() -> LLMProvider:
    return PROVIDERS.get(os.getenv("FORGEX_LLM_PROVIDER", "none"), NullProvider)()

def sanitize(raw: list[dict], valid_ids: set[str]) -> list[dict]:
    out = []
    for h in raw or []:
        ids = [e for e in h.get("evidence_ids", []) if e in valid_ids]
        if not (h.get("title") and h.get("explanation") and ids):
            continue  # reject claims not grounded in real evidence
        out.append({"title": str(h["title"])[:200], "explanation": str(h["explanation"])[:1500], "evidence_ids": ids,
                    "alternatives": list(h.get("alternatives", []))[:5], "investigation": list(h.get("investigation", []))[:5],
                    "source": "llm", "status": "unverified_ai_suggestion", "confidence": None, "confidence_label": "unscored"})
    return out
