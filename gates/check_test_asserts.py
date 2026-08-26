"""Fail any test function containing no assert and no pytest.raises/warns/fail.

A test that cannot fail is not a test; under an agent optimizing for green,
it is a liability. Usage: python gates/check_test_asserts.py [dir ...]
"""

import ast
import sys
from pathlib import Path

ASSERTING_CALLS = {"raises", "warns", "fail", "xfail", "approx"}


def has_assertion(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ASSERTING_CALLS:
                return True
    return False


def main(paths: list[str]) -> int:
    bad = []
    for p in paths or ["tests"]:
        for f in sorted(Path(p).rglob("test_*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name.startswith("test_"):
                    if not has_assertion(node):
                        bad.append(
                            f"{f}:{node.lineno}: {node.name} has no assert and "
                            "no pytest.raises"
                        )
    if bad:
        print("ASSERT-FREE TESTS (a test that cannot fail is not a test):")
        print("\n".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
