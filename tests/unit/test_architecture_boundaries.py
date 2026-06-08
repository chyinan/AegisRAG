import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_DOMAIN_IMPORTS = {
    "boto3",
    "deepseek",
    "fastapi",
    "httpx",
    "langchain",
    "langgraph",
    "minio",
    "openai",
    "qwen",
    "redis",
    "sqlalchemy",
}
FORBIDDEN_COMMON_IMPORTS = FORBIDDEN_DOMAIN_IMPORTS | {"starlette"}
ROUTE_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "route"}
ROUTE_BINDING_NAMES = {"app", "api", "api_router", "router"}
ALLOWED_FASTAPI_IMPORT_FILES = {
    PROJECT_ROOT / "apps" / "api" / "main.py",
    PROJECT_ROOT / "apps" / "api" / "dependencies.py",
    PROJECT_ROOT / "apps" / "api" / "error_handlers.py",
    PROJECT_ROOT / "apps" / "api" / "middleware.py",
    PROJECT_ROOT / "apps" / "api" / "service_dependencies.py",
}
ALLOWED_ROUTE_DECLARATION_DIR = PROJECT_ROOT / "apps" / "api" / "routes"
FORBIDDEN_ROUTE_INFRASTRUCTURE_IMPORTS = {
    "packages.data.adapters",
    "packages.data.queue.adapters",
    "packages.data.storage",
    "packages.llm.adapters",
    "packages.vectorstores.adapters",
    "deepseek",
    "ollama",
    "openai",
    "qwen",
    "vllm",
    "sqlalchemy",
}
FORBIDDEN_LLM_IMPORTS = {
    "boto3",
    "deepseek",
    "fastapi",
    "httpx",
    "minio",
    "ollama",
    "openai",
    "qwen",
    "redis",
    "sqlalchemy",
    "vllm",
}
FORBIDDEN_LLM_MODULE_IMPORTS = {
    "apps.api",
    "packages.retrieval.storage",
    "packages.vectorstores.adapters",
}
FORBIDDEN_RAG_STREAMING_IMPORTS = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "redis",
    "minio",
    "boto3",
    "httpx",
    "openai",
    "qwen",
    "deepseek",
    "ollama",
    "vllm",
}
FORBIDDEN_MEMORY_SERVICE_IMPORTS = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "redis",
    "minio",
    "boto3",
    "httpx",
    "openai",
    "qwen",
    "deepseek",
    "ollama",
    "vllm",
}
FORBIDDEN_MEMORY_SERVICE_MODULES = {
    "packages.llm",
    "packages.vectorstores",
    "packages.retrieval.storage",
    "apps.api",
}
FORBIDDEN_AGENT_IMPORTS = {
    "boto3",
    "deepseek",
    "fastapi",
    "httpx",
    "langchain",
    "langgraph",
    "minio",
    "ollama",
    "openai",
    "qwen",
    "redis",
    "sqlalchemy",
    "starlette",
    "vllm",
}
FORBIDDEN_AGENT_MODULES = {
    "apps.api",
    "packages.data.storage",
    "packages.embeddings",
    "packages.llm",
    "packages.rag",
    "packages.retrieval",
    "packages.vectorstores",
}
AGENT_TOOLS_DIR = PROJECT_ROOT / "packages" / "agent" / "tools"
AGENT_STORAGE_DIR = PROJECT_ROOT / "packages" / "agent" / "storage"
CALCULATOR_TOOL_FILE = AGENT_TOOLS_DIR / "calculator.py"
FILE_READER_TOOL_FILE = AGENT_TOOLS_DIR / "file_reader.py"
ALLOWED_AGENT_TOOL_RETRIEVAL_MODULES = {
    "packages.retrieval.application",
    "packages.retrieval.application.RetrieveApplicationService",
    "packages.retrieval.application.RetrieveCandidateResponse",
    "packages.retrieval.application.RetrieveCommand",
    "packages.retrieval.application.RetrieveResponse",
    "packages.retrieval.exceptions",
    "packages.retrieval.exceptions.RETRIEVAL_FORBIDDEN_FILTER",
    "packages.retrieval.exceptions.RetrievalError",
}
FORBIDDEN_AGENT_TOOL_ADAPTER_MODULES = {
    "apps.api",
    "packages.data.storage",
    "packages.embeddings",
    "packages.llm",
    "packages.rag",
    "packages.retrieval.dense",
    "packages.retrieval.filters",
    "packages.retrieval.rerank",
    "packages.retrieval.rrf",
    "packages.retrieval.service",
    "packages.retrieval.sparse",
    "packages.retrieval.storage",
    "packages.vectorstores",
    "tests.eval",
}
FORBIDDEN_CALCULATOR_IMPORTS = FORBIDDEN_AGENT_IMPORTS | {
    "importlib",
    "os",
    "pathlib",
    "socket",
    "subprocess",
}
FORBIDDEN_CALCULATOR_MODULES = FORBIDDEN_AGENT_TOOL_ADAPTER_MODULES | {
    "packages.common.config",
    "packages.data",
    "packages.retrieval",
}
FORBIDDEN_FILE_READER_MODULES = FORBIDDEN_AGENT_TOOL_ADAPTER_MODULES | {
    "packages.common.config",
    "packages.data",
    "packages.retrieval",
}


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.py") if ".venv" not in path.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_root(name: str) -> str:
    return name.split(".", maxsplit=1)[0]


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(_import_root(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(_import_root(node.module))
    return roots


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            modules.add(node.args[0].value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            modules.add(node.args[0].value)
    return modules


def _is_domain_file(path: Path) -> bool:
    relative = path.relative_to(PROJECT_ROOT)
    parts = relative.parts
    if len(parts) < 3 or parts[0] != "packages":
        return False

    package_relative_parts = parts[2:]
    return package_relative_parts[0] == "domain" or (
        len(package_relative_parts) == 1 and package_relative_parts[0].startswith("domain")
    )


def test_domain_layer_does_not_import_infrastructure_or_frameworks() -> None:
    violations: list[str] = []
    for path in _python_files(PROJECT_ROOT / "packages"):
        if not _is_domain_file(path):
            continue
        forbidden = sorted(_imported_roots(_parse(path)) & FORBIDDEN_DOMAIN_IMPORTS)
        if forbidden:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {', '.join(forbidden)}")

    assert violations == []


def test_common_layer_does_not_import_frameworks_or_infrastructure() -> None:
    violations: list[str] = []
    for path in _python_files(PROJECT_ROOT / "packages" / "common"):
        forbidden = sorted(_imported_roots(_parse(path)) & FORBIDDEN_COMMON_IMPORTS)
        if forbidden:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {', '.join(forbidden)}")

    assert violations == []


def _decorator_route_method(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _decorator_route_method(node.func)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in ROUTE_BINDING_NAMES:
            return node.attr
    return None


def _declares_fastapi_routes(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "APIRouter":
                return True
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                if _decorator_route_method(decorator) in ROUTE_METHODS:
                    return True
    return False


def test_fastapi_route_declarations_stay_in_api_routes_or_main_registration() -> None:
    violations: list[str] = []
    for root in (PROJECT_ROOT / "apps", PROJECT_ROOT / "packages"):
        for path in _python_files(root):
            if ALLOWED_ROUTE_DECLARATION_DIR in path.parents:
                continue

            tree = _parse(path)
            if path in ALLOWED_FASTAPI_IMPORT_FILES:
                if _declares_fastapi_routes(tree):
                    violations.append(str(path.relative_to(PROJECT_ROOT)))
                continue

            imports_fastapi = "fastapi" in _imported_roots(tree)
            if imports_fastapi or _declares_fastapi_routes(tree):
                violations.append(str(path.relative_to(PROJECT_ROOT)))

    assert violations == []


def test_domain_file_detector_covers_supported_domain_file_shapes() -> None:
    assert _is_domain_file(PROJECT_ROOT / "packages" / "common" / "domain.py")
    assert _is_domain_file(PROJECT_ROOT / "packages" / "common" / "domain_models.py")
    assert _is_domain_file(PROJECT_ROOT / "packages" / "common" / "domain" / "__init__.py")
    assert not _is_domain_file(PROJECT_ROOT / "packages" / "common" / "application.py")
    assert not _is_domain_file(PROJECT_ROOT / "packages" / "data" / "storage" / "base.py")
    assert not _is_domain_file(
        PROJECT_ROOT / "packages" / "common" / "application" / "domain_service.py"
    )


def test_route_decorator_detector_ignores_unrelated_get_decorators() -> None:
    tree = ast.parse(
        """
@cache.get("key")
def cached_value() -> str:
    return "value"
"""
    )

    assert not _declares_fastapi_routes(tree)


def test_fastapi_support_file_allowlist_does_not_allow_route_declarations() -> None:
    tree = ast.parse(
        """
from fastapi import APIRouter

router = APIRouter()

@router.get("/bad")
def bad_route() -> dict[str, str]:
    return {"status": "bad"}
"""
    )

    assert _declares_fastapi_routes(tree)


def test_api_route_modules_do_not_wire_infrastructure_adapters() -> None:
    violations: list[str] = []
    for path in _python_files(ALLOWED_ROUTE_DECLARATION_DIR):
        imported_modules = _imported_modules(_parse(path))
        forbidden = sorted(
            module
            for module in imported_modules
            if any(
                module == forbidden_module or module.startswith(f"{forbidden_module}.")
                for forbidden_module in FORBIDDEN_ROUTE_INFRASTRUCTURE_IMPORTS
            )
        )
        if forbidden:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {', '.join(forbidden)}")

    assert violations == []


def test_llm_provider_package_stays_provider_neutral_and_framework_free() -> None:
    violations: list[str] = []
    for path in _python_files(PROJECT_ROOT / "packages" / "llm"):
        tree = _parse(path)
        imported_roots = _imported_roots(tree)
        forbidden = sorted(imported_roots & FORBIDDEN_LLM_IMPORTS)
        imported_modules = _imported_modules(tree)
        forbidden_modules = sorted(
            module
            for module in imported_modules
            if any(
                module == forbidden_module or module.startswith(f"{forbidden_module}.")
                for forbidden_module in FORBIDDEN_LLM_MODULE_IMPORTS
            )
        )
        if forbidden:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {', '.join(forbidden)}")
        if forbidden_modules:
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)} imports {', '.join(forbidden_modules)}"
            )

    assert violations == []


def test_rag_streaming_formatter_stays_framework_and_infrastructure_free() -> None:
    path = PROJECT_ROOT / "packages" / "rag" / "streaming.py"
    tree = _parse(path)

    forbidden = sorted(_imported_roots(tree) & FORBIDDEN_RAG_STREAMING_IMPORTS)

    assert forbidden == []


def test_memory_service_stays_framework_provider_and_vectorstore_free() -> None:
    path = PROJECT_ROOT / "packages" / "memory" / "service.py"
    tree = _parse(path)

    forbidden_roots = sorted(_imported_roots(tree) & FORBIDDEN_MEMORY_SERVICE_IMPORTS)
    imported_modules = _imported_modules(tree)
    forbidden_modules = sorted(
        module
        for module in imported_modules
        if any(
            module == forbidden_module or module.startswith(f"{forbidden_module}.")
            for forbidden_module in FORBIDDEN_MEMORY_SERVICE_MODULES
        )
    )

    assert forbidden_roots == []
    assert forbidden_modules == []


def test_memory_sqlalchemy_imports_are_limited_to_storage_layer() -> None:
    violations: list[str] = []
    memory_root = PROJECT_ROOT / "packages" / "memory"
    storage_root = memory_root / "storage"
    for path in _python_files(memory_root):
        if storage_root in path.parents:
            continue
        if "sqlalchemy" in _imported_roots(_parse(path)):
            violations.append(str(path.relative_to(PROJECT_ROOT)))

    assert violations == []


def test_agent_package_stays_framework_provider_and_infrastructure_free() -> None:
    violations: list[str] = []
    for path in _python_files(PROJECT_ROOT / "packages" / "agent"):
        if AGENT_TOOLS_DIR in path.parents:
            continue
        if AGENT_STORAGE_DIR in path.parents:
            continue
        tree = _parse(path)
        forbidden_roots = sorted(_imported_roots(tree) & FORBIDDEN_AGENT_IMPORTS)
        imported_modules = _imported_modules(tree)
        forbidden_modules = sorted(
            module
            for module in imported_modules
            if any(
                module == forbidden_module or module.startswith(f"{forbidden_module}.")
                for forbidden_module in FORBIDDEN_AGENT_MODULES
            )
        )
        if forbidden_roots:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_roots}")
        if forbidden_modules:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_modules}")

    assert violations == []


def test_agent_sqlalchemy_imports_are_limited_to_storage_layer() -> None:
    violations: list[str] = []
    agent_root = PROJECT_ROOT / "packages" / "agent"
    for path in _python_files(agent_root):
        if AGENT_STORAGE_DIR in path.parents:
            continue
        if "sqlalchemy" in _imported_roots(_parse(path)):
            violations.append(str(path.relative_to(PROJECT_ROOT)))

    assert violations == []


def test_agent_tool_adapters_keep_retrieval_dependencies_narrow() -> None:
    violations: list[str] = []
    for path in _python_files(AGENT_TOOLS_DIR):
        imported_modules = _imported_modules(_parse(path))
        retrieval_modules = sorted(
            module
            for module in imported_modules
            if module == "packages.retrieval" or module.startswith("packages.retrieval.")
        )
        forbidden_retrieval_modules = [
            module
            for module in retrieval_modules
            if module not in ALLOWED_AGENT_TOOL_RETRIEVAL_MODULES
        ]
        forbidden_roots = sorted(_imported_roots(_parse(path)) & FORBIDDEN_AGENT_IMPORTS)
        forbidden_modules = sorted(
            module
            for module in imported_modules
            if any(
                module == forbidden_module or module.startswith(f"{forbidden_module}.")
                for forbidden_module in FORBIDDEN_AGENT_TOOL_ADAPTER_MODULES
            )
        )
        if forbidden_roots:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_roots}")
        if forbidden_retrieval_modules:
            violations.append(
                f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_retrieval_modules}"
            )
        if forbidden_modules:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_modules}")

    assert violations == []


def test_calculator_tool_stays_pure_compute_adapter() -> None:
    tree = _parse(CALCULATOR_TOOL_FILE)
    forbidden_roots = sorted(_imported_roots(tree) & FORBIDDEN_CALCULATOR_IMPORTS)
    imported_modules = _imported_modules(tree)
    forbidden_modules = sorted(
        module
        for module in imported_modules
        if any(
            module == forbidden_module or module.startswith(f"{forbidden_module}.")
            for forbidden_module in FORBIDDEN_CALCULATOR_MODULES
        )
    )

    assert forbidden_roots == []
    assert forbidden_modules == []


def test_file_reader_tool_stays_narrow_local_file_adapter() -> None:
    tree = _parse(FILE_READER_TOOL_FILE)
    forbidden_roots = sorted(_imported_roots(tree) & FORBIDDEN_AGENT_IMPORTS)
    imported_modules = _imported_modules(tree)
    forbidden_modules = sorted(
        module
        for module in imported_modules
        if any(
            module == forbidden_module or module.startswith(f"{forbidden_module}.")
            for forbidden_module in FORBIDDEN_FILE_READER_MODULES
        )
    )

    assert forbidden_roots == []
    assert forbidden_modules == []


def test_chat_route_stays_thin_and_avoids_storage_or_rag_internals() -> None:
    path = PROJECT_ROOT / "apps" / "api" / "routes" / "chat.py"
    imported_modules = _imported_modules(_parse(path))
    forbidden_modules = sorted(
        module
        for module in imported_modules
        if any(
            module == forbidden_module or module.startswith(f"{forbidden_module}.")
            for forbidden_module in {
                "packages.memory.storage",
                "packages.data.storage",
                "packages.llm",
                "packages.vectorstores",
                "packages.retrieval",
                "sqlalchemy",
            }
        )
    )

    assert forbidden_modules == []


def test_agent_route_stays_thin_and_avoids_storage_tools_or_provider_internals() -> None:
    path = PROJECT_ROOT / "apps" / "api" / "routes" / "agent.py"
    imported_modules = _imported_modules(_parse(path))
    forbidden_modules = sorted(
        module
        for module in imported_modules
        if any(
            module == forbidden_module or module.startswith(f"{forbidden_module}.")
            for forbidden_module in {
                "packages.agent.storage",
                "packages.agent.tools",
                "packages.data.storage",
                "packages.embeddings",
                "packages.llm",
                "packages.retrieval",
                "packages.vectorstores",
                "sqlalchemy",
            }
        )
    )

    assert forbidden_modules == []


def test_openwebui_and_sources_routes_stay_thin_and_avoid_infrastructure() -> None:
    violations: list[str] = []
    for filename in ("openwebui.py", "sources.py"):
        path = PROJECT_ROOT / "apps" / "api" / "routes" / filename
        imported_modules = _imported_modules(_parse(path))
        forbidden_modules = sorted(
            module
            for module in imported_modules
            if any(
                module == forbidden_module or module.startswith(f"{forbidden_module}.")
                for forbidden_module in {
                    "packages.data.storage",
                    "packages.llm",
                    "packages.vectorstores",
                    "packages.retrieval",
                    "sqlalchemy",
                }
            )
        )
        if forbidden_modules:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_modules}")

    assert violations == []


def test_openwebui_and_source_resolver_services_stay_framework_and_infra_free() -> None:
    violations: list[str] = []
    for filename in ("openwebui.py", "source_resolver.py"):
        path = PROJECT_ROOT / "packages" / "rag" / filename
        tree = _parse(path)
        forbidden_roots = sorted(_imported_roots(tree) & FORBIDDEN_RAG_STREAMING_IMPORTS)
        imported_modules = _imported_modules(tree)
        forbidden_modules = sorted(
            module
            for module in imported_modules
            if any(
                module == forbidden_module or module.startswith(f"{forbidden_module}.")
                for forbidden_module in {
                    "apps.api",
                    "packages.data.storage",
                    "packages.llm.adapters",
                    "packages.vectorstores.adapters",
                }
            )
        )
        if forbidden_roots:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_roots}")
        if forbidden_modules:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_modules}")

    assert violations == []


def test_production_packages_do_not_import_eval_test_modules() -> None:
    violations: list[str] = []
    for path in _python_files(PROJECT_ROOT / "packages"):
        imported_modules = _imported_modules(_parse(path))
        forbidden_modules = sorted(
            module
            for module in imported_modules
            if module == "tests.eval" or module.startswith("tests.eval.")
        )
        if forbidden_modules:
            violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {forbidden_modules}")

    assert violations == []


def test_eval_import_boundary_detector_catches_import_from_and_dynamic_imports() -> None:
    tree = ast.parse(
        """
import importlib

from tests import eval

one = importlib.import_module("tests.eval.rag.loader")
two = __import__("tests.eval.reporting")
"""
    )

    imported_modules = _imported_modules(tree)

    assert "tests.eval" in imported_modules
    assert "tests.eval.rag.loader" in imported_modules
    assert "tests.eval.reporting" in imported_modules
