"""Dependency-direction enforcement (05_AGENT_INSTRUCTIONS.md, 09_CODING_STANDARDS.md).

Core may import contract definitions; core must never import vendor
implementations (adapters/) or client code (tui/), and must stay stdlib-only
so the runtime has no hidden third-party coupling. Contracts may import core
(contracts model the run context they receive) but nothing else beyond stdlib.
Enforced mechanically here — this file is the CI gate.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED_ROOTS = {"core", "contracts"}
VENDOR_ROOTS = {"adapters", "tui"}


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


def test_core_imports_are_stdlib_or_contracts_only():
    violations: list[str] = []
    for path in _modules("core"):
        for root in _imported_roots(path) - ALLOWED_ROOTS:
            if root not in sys.stdlib_module_names:
                violations.append(f"{path.name} imports {root!r}")
    assert not violations, f"vendor/third-party imports in core/: {violations}"


def test_contracts_imports_are_stdlib_or_core_only():
    violations: list[str] = []
    for path in _modules("contracts"):
        for root in _imported_roots(path) - ALLOWED_ROOTS:
            if root not in sys.stdlib_module_names:
                violations.append(f"{path.name} imports {root!r}")
    assert not violations, f"unexpected imports in contracts/: {violations}"


def test_no_adapter_or_tui_import_anywhere_in_core_or_contracts():
    violations: list[str] = []
    for root_dir in ALLOWED_ROOTS:
        for path in _modules(root_dir):
            bad = _imported_roots(path) & VENDOR_ROOTS
            if bad:
                violations.append(f"{root_dir}/{path.name} imports {sorted(bad)}")
    assert not violations, f"dependency-direction violation: {violations}"


def test_future_import_present_in_all_core_and_contract_modules():
    # serialization/typing discipline: every module opts into modern annotations
    missing = [
        f"{root_dir}/{path.name}"
        for root_dir in ALLOWED_ROOTS
        for path in _modules(root_dir)
        if "__future__" not in _imported_roots(path) and path.stat().st_size > 0
    ]
    assert not missing, f"missing `from __future__ import annotations`: {missing}"
