"""Р15: бот без ИИ — ни зависимостей, ни импортов SDK языковых моделей (ТЗ, раздел 1)."""
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
AI_PACKAGES = re.compile(r"\b(anthropic|openai|google[-.]generativeai|google[-.]genai|langchain\w*|mistralai|cohere"
                         r"|ollama|llama_index|transformers)\b", re.IGNORECASE)
IMPORT_PREFIXES = ("import ", "from ")


def test_no_language_model_packages_in_requirements():
    for name in ("requirements.txt", "requirements-dev.txt"):
        assert not AI_PACKAGES.search((ROOT / name).read_text(encoding="utf-8")), name


def test_no_language_model_imports_in_code():
    for path in (ROOT / "bot").rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        imports = [line for line in lines if line.lstrip().startswith(IMPORT_PREFIXES)]
        assert not any(AI_PACKAGES.search(line) for line in imports), path
