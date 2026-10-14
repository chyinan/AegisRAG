from __future__ import annotations

import subprocess
import sys


def test_adapter_modules_import_without_optional_dependencies() -> None:
    program = """
import importlib.abc
import sys

class BlockFrameworks(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = {"langchain", "langchain_core", "langchain_openai", "langsmith"}
        if fullname.split(".")[0] in roots:
            raise ModuleNotFoundError("optional dependency intentionally unavailable")
sys.meta_path.insert(0, BlockFrameworks())
from packages.llm.adapters.langchain import LangChainChatProvider
from packages.embeddings.adapters.langchain import LangChainEmbeddingProvider
for cls in (LangChainChatProvider, LangChainEmbeddingProvider):
    try:
        cls(provider="openai_compatible", model="test",
            base_url="https://models.test/v1", api_key="test")
    except RuntimeError as exc:
        assert "install the 'langchain' extra" in str(exc)
    else:
        raise AssertionError("missing dependencies must give an actionable error")
"""
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False, timeout=20
    )
    assert result.returncode == 0, result.stderr
