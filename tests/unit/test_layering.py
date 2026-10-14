"""Package layering and the core/ move stay as documented in docs/architecture.md."""
import ast
from pathlib import Path

import wechat_bridge

PACKAGE = Path(wechat_bridge.__file__).parent
FOUNDATION = {"core"}
# Packages a package may import at module level (docs/architecture.md, "Dependency direction").
ALLOWED = {
    "core": set(),
    "source": {"core"},
    "adapters": {"core", "source"},
    "attachments": {"core", "source", "adapters"},
    "archive": {"core", "source", "adapters", "attachments"},
    "search": {"core", "source", "adapters", "attachments", "archive"},
    "mcp": {"core", "source", "adapters", "attachments", "archive", "search"},
    "setup": {"core", "source"},
}
# Documented function-local exceptions: (importing module, imported package or module).
LOCAL_EXCEPTIONS = {
    ("archive/importer.py", "search"), ("archive/store.py", "search"),
    ("adapters/wechat_4_1.py", "attachments"), ("mcp/service.py", "cli"),
}


def imports(path):
    """Yield (top-level target package or module, is_function_local) for each import in a file."""
    rel = path.relative_to(PACKAGE).parts[:-1]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    local = {id(n) for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
             for n in ast.walk(f) if isinstance(n, (ast.Import, ast.ImportFrom))}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = list(rel[: len(rel) - (node.level - 1)]) if node.level > 1 else list(rel)
            parts = base + (node.module.split(".") if node.module else [])
            if not parts:  # `from . import x` / `from .. import x` at package root
                parts = [a.name for a in node.names][:1]
            yield parts[0], id(node) in local
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "wechat_bridge":
            yield (node.module.split(".") + [a.name for a in node.names])[1], id(node) in local
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "wechat_bridge" and "." in alias.name:
                    yield alias.name.split(".")[1], id(node) in local


def sources():
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.parent != PACKAGE and path.name != "lldb_capture.py":
            yield path, path.relative_to(PACKAGE).parts[0], path.relative_to(PACKAGE).as_posix()


def test_module_level_imports_follow_the_documented_layering():
    violations = []
    for path, package, rel in sources():
        for target, is_local in imports(path):
            if is_local or target == package or target not in (*ALLOWED, "cli", "proof"):
                continue
            if target not in ALLOWED[package]:
                violations.append(f"{rel} -> {target}")
    assert violations == []


def test_function_local_upward_imports_are_only_the_documented_exceptions():
    seen = set()
    for path, package, rel in sources():
        for target, is_local in imports(path):
            if is_local and target != package and target not in ALLOWED[package] and target in (*ALLOWED, "cli", "proof"):
                seen.add((rel, target))
    assert seen <= LOCAL_EXCEPTIONS, sorted(seen - LOCAL_EXCEPTIONS)


def test_foundation_imports_nothing_above_it():
    for path in (PACKAGE / "core").glob("*.py"):
        assert not {t for t, _ in imports(path)} - FOUNDATION - {path.parent.name}, path.name


def test_foundation_modules_live_only_under_core():
    names = {"config", "errors", "limits", "serialize", "validate", "clock"}
    assert {p.stem for p in PACKAGE.glob("*.py")} & names == set()
    assert {p.stem for p in (PACKAGE / "core").glob("*.py")} >= names | {"__init__"}


def test_lldb_capture_stays_standard_library_only():
    tree = ast.parse((PACKAGE / "setup/lldb_capture.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module.split(".")[0] != "wechat_bridge"
        elif isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "wechat_bridge" for a in node.names)
