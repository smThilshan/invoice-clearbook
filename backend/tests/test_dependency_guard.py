"""Hard rule: no third-party LLM APIs. PaddleOCR's toolkit (PaddleX) installs and loads
OpenAI/LangChain client code (decision 22); our own code must never use it."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
BANNED = ("openai", "langchain", "anthropic", "google.generativeai", "cohere", "mistralai")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_app_code_never_imports_llm_api_clients() -> None:
    offenders = [
        f"{path.relative_to(APP.parent)}: {module}"
        for path in APP.rglob("*.py")
        for module in imported_modules(path)
        if module.startswith(BANNED)
    ]

    assert offenders == []


def test_guard_detects_banned_imports(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text("import openai\nfrom langchain_openai import ChatOpenAI\nimport os\n")

    assert imported_modules(sample) == {"openai", "langchain_openai", "os"}
    assert {m for m in imported_modules(sample) if m.startswith(BANNED)} == {
        "openai",
        "langchain_openai",
    }
