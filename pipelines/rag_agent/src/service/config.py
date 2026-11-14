"""
Service-layer configuration: API keys/URLs and model paths read from environment variables.
"""

import os


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(
            f"Missing environment variable {name}: the API key is now injected via environment variables; please export {name}=sk-... before startup"
        )
    return value


def _env_or(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


DEEPSEEK_API_KEY = _require_env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env_or("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

QWEN_API_KEY = _require_env("QWEN_API_KEY")
QWEN_BASE_URL = _env_or("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

VDB_PATH = _env_or("RAG_VDB_PATH", "/path/to/qagent/vdb")

EMBEDDING_MODEL_PATH = _env_or("RAG_EMBEDDING_MODEL", "/path/to/models/bge-embedding-large-en")
RERANKER_MODEL_PATH = _env_or("RAG_RERANKER_MODEL", "/path/to/models/bge-reranker-large")

# ============================================================
# Unified LLM entry: route by model name to the client wired to that
# model's endpoint/key. Clients are constructed once at import time and
# only used read-only afterwards (safe to share across request threads).
# ============================================================
from openai import OpenAI  # noqa: E402

qwen_client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)
deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

_LLM_CLIENTS = {
    "qwen3-32b": qwen_client,
    "deepseek-v4-flash": deepseek_client,
}


def LLM_client(*, model, **kwargs):
    """Unified chat-completions entry; unknown model names raise KeyError."""
    return _LLM_CLIENTS[model].chat.completions.create(model=model, **kwargs)
