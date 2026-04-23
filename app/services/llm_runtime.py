"""
Local-only provider selection for RailMan AI chat generation.
"""
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    text: str
    provider: str
    model: str


def _provider_order() -> List[str]:
    raw = os.getenv("RAILMAN_LLM_PROVIDER_ORDER", "local,rule_based")
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    allowed = [provider for provider in requested if provider in {"local", "rule_based"}]
    if not allowed:
        return ["local", "rule_based"]
    if "local" not in allowed:
        allowed.insert(0, "local")
    return allowed


def _local_model_path() -> Path:
    raw = os.getenv("LOCAL_LLM_MODEL_PATH", "models/railman-chat.gguf").strip()
    return Path(raw)


def get_runtime_status() -> dict:
    model_path = _local_model_path()
    return {
        "provider_order": _provider_order(),
        "local_model_path": str(model_path),
        "local_model_exists": model_path.exists(),
        "offline_only": True,
    }


def _messages_to_prompt(system_prompt: str, messages: List[dict]) -> str:
    sections = [
        "You are in a multi-turn conversation. Reply only as ASSISTANT.\n",
        f"SYSTEM:\n{system_prompt.strip()}",
    ]
    for message in messages:
        role = message.get("role", "user").upper()
        content = message.get("content", "").strip()
        if content:
            sections.append(f"{role}:\n{content}")
    sections.append("ASSISTANT:\n")
    return "\n\n".join(sections)


def _clean_local_output(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^(ASSISTANT|Assistant):\s*", "", cleaned)
    cleaned = re.split(r"\n(?:USER|SYSTEM):", cleaned, maxsplit=1)[0].strip()
    return cleaned


@lru_cache(maxsize=1)
def _load_local_model():
    enabled = os.getenv("LOCAL_LLM_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    model_path = _local_model_path()
    if not enabled or not model_path.exists():
        return None

    try:
        from llama_cpp import Llama
    except ImportError:
        logger.info("Local model requested but llama-cpp-python is not installed")
        return None

    try:
        return Llama(
            model_path=str(model_path),
            n_ctx=int(os.getenv("LOCAL_LLM_N_CTX", "4096")),
            n_threads=int(os.getenv("LOCAL_LLM_THREADS", "6")),
            n_gpu_layers=int(os.getenv("LOCAL_LLM_GPU_LAYERS", "0")),
            verbose=False,
        )
    except Exception as exc:
        logger.warning("Failed to load local model from %s: %s", model_path, exc)
        return None


def _generate_local(system_prompt: str, messages: List[dict]) -> Optional[GenerationResult]:
    model = _load_local_model()
    if model is None:
        return None

    prompt = _messages_to_prompt(system_prompt, messages)
    try:
        response = model(
            prompt,
            max_tokens=int(os.getenv("LOCAL_LLM_MAX_TOKENS", "320")),
            temperature=float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.2")),
            top_p=float(os.getenv("LOCAL_LLM_TOP_P", "0.9")),
            stop=["\nUSER:", "\nSYSTEM:", "\n\nUSER:", "\n\nSYSTEM:"],
        )
        text = _clean_local_output(response["choices"][0]["text"])
        if not text:
            return None
        return GenerationResult(text=text, provider="local", model=_local_model_path().name)
    except Exception as exc:
        logger.warning("Local model generation failed: %s", exc)
        return None


def generate_with_providers(system_prompt: str, messages: List[dict]) -> Optional[GenerationResult]:
    for provider in _provider_order():
        if provider == "local":
            result = _generate_local(system_prompt, messages)
        else:
            result = None

        if result is not None:
            return result

    return None
