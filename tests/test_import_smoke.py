"""Guard against import-time SyntaxError in core modules.

Regression for the Python2-style `except A, B:` syntax errors that broke
the startup import chain. Uses ast.parse so the test fails fast at
collection time without needing a live DB.
"""

import ast
import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Modules on the startup import chain. If any fail to parse, the gateway
# cannot start. Listed explicitly so a new SyntaxError surfaces as a named
# test failure rather than an opaque ImportError.
STARTUP_MODULES = [
    "llm_gateway.main",
    "llm_gateway.api.proxy",
    "llm_gateway.services.upstream_routing",
    "llm_gateway.services.runtime_metrics",
    "llm_gateway.services.policy",
]


@pytest.mark.parametrize("module_name", STARTUP_MODULES)
def test_startup_module_parses(module_name: str) -> None:
    """Each startup-path module must be syntactically valid Python 3."""
    rel = module_name.replace(".", "/") + ".py"
    path = SRC / rel
    source = path.read_text()
    ast.parse(source)


def test_compileall_src_tree() -> None:
    """Every .py file under src/ must compile."""
    errors = []
    for py in SRC.rglob("*.py"):
        try:
            ast.parse(py.read_text(), filename=str(py))
        except SyntaxError as exc:
            errors.append(f"{py}: {exc}")
    assert not errors, "SyntaxErrors found:\n" + "\n".join(errors)


def test_all_startup_modules_importable() -> None:
    """Importable check (catches import-time errors beyond syntax)."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    for module_name in STARTUP_MODULES:
        importlib.import_module(module_name)
