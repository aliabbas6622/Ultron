"""Dependency-direction enforcement (05_AGENT_INSTRUCTIONS.md, 09_CODING_STANDARDS.md).

The LEGO rule, enforced mechanically:

- contracts/ is SELF-CONTAINED: stdlib + contracts only. No core, no runtime,
  no third-party packages. This is what makes every brick portable to any host.
- tools/ is GRAB-AND-GO: stdlib + contracts + tools only. Never core — you can
  copy contracts/ + tools/ into any agent and it works.
- core/ (the reference runtime) may import contracts + stdlib, never vendor
  implementations (adapters/) or client code (tui/) and never tool bricks
  (tools/) — the runtime receives blocks by injection.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# package -> what its modules may import beyond stdlib
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "contracts": {"contracts"},
    "tools": {"contracts", "tools"},
    "providers": {"contracts", "providers"},
    "core": {"core", "contracts"},
}
FORBIDDEN_EVERYWHERE = {"adapters", "tui"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _modules(root: str) -> list[Path]:
    return sorted((REPO / root).glob("*.py"))


def test_contracts_is_self_contained():
    """contracts/ must build without any runtime — this is the brick interface spec."""
    violations: list[str] = []
    for path in _modules("contracts"):
        for root in _imported_roots(path) - ALLOWED_IMPORTS["contracts"]:
            if root not in sys.stdlib_module_names:
                violations.append(f"contracts/{path.name} imports {root!r}")
    assert not violations, f"contracts/ is not self-contained: {violations}"


def test_tools_is_grab_and_go():
    """tools/ bricks must run anywhere: contracts/ + stdlib only, never core."""
    violations: list[str] = []
    for path in _modules("tools"):
        for root in _imported_roots(path) - ALLOWED_IMPORTS["tools"]:
            if root not in sys.stdlib_module_names:
                violations.append(f"tools/{path.name} imports {root!r}")
    assert not violations, f"tools/ is not grab-and-go: {violations}"


def test_providers_is_grab_and_go():
    """providers/ bricks must run anywhere: contracts/ + stdlib only, never core."""
    violations: list[str] = []
    for path in _modules("providers"):
        for root in _imported_roots(path) - ALLOWED_IMPORTS["providers"]:
            if root not in sys.stdlib_module_names:
                violations.append(f"providers/{path.name} imports {root!r}")
    assert not violations, f"providers/ is not grab-and-go: {violations}"


def test_core_imports_are_stdlib_or_contracts_only():
    """core is pure composition: no vendors, no clients, no brick implementations."""
    violations: list[str] = []
    for path in _modules("core"):
        for root in _imported_roots(path) - ALLOWED_IMPORTS["core"]:
            if root not in sys.stdlib_module_names:
                violations.append(f"core/{path.name} imports {root!r}")
    assert not violations, f"unexpected imports in core/: {violations}"


def test_no_adapter_or_tui_import_anywhere_in_core_contracts_tools_or_providers():
    violations: list[str] = []
    for root_dir in ("core", "contracts", "tools", "providers"):
        for path in _modules(root_dir):
            bad = _imported_roots(path) & FORBIDDEN_EVERYWHERE
            if bad:
                violations.append(f"{root_dir}/{path.name} imports {sorted(bad)}")
    assert not violations, f"dependency-direction violation: {violations}"


def test_future_import_present_in_all_core_contract_tool_and_provider_modules():
    # serialization/typing discipline: every module opts into modern annotations
    missing = [
        f"{root_dir}/{path.name}"
        for root_dir in ("core", "contracts", "tools", "providers")
        for path in _modules(root_dir)
        if "__future__" not in _imported_roots(path) and path.stat().st_size > 0
    ]
    assert not missing, f"missing `from __future__ import annotations`: {missing}"
