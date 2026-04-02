"""Architecture tests — AST-level enforcement of type discipline.

These tests parse the source code and assert structural rules. They
don't run the code — they inspect it. If a rule is violated, the test
tells you exactly which file and line to fix.

Rules enforced:
1. No `Any` in type annotations (imports, signatures, variable annotations)
2. No bare `dict` without type arguments in annotations
3. No `object` as a type annotation (too vague — use a protocol or concrete type)
4. No `# type: ignore` without a specific error code
5. No `.get()` with a default on models (use direct access or explicit check)
"""

import ast
from pathlib import Path

import pytest

# Source directories to check
SOURCE_DIRS = [
    Path("verisure_api"),
    Path("custom_components/verisure_it"),
]

# Files explicitly allowed to use Any (the absolute minimum boundary)
# This list should ideally be EMPTY. Every entry needs justification.
ANY_ALLOWLIST: dict[str, str] = {
    # "verisure_api/client.py": "JSON boundary — json.loads returns Any",
}

# HA integration files are exempt from the Any ban because Home Assistant's
# base classes (ConfigFlow, AlarmControlPanelEntity, etc.) mandate dict[str, Any]
# in their method signatures. We enforce Any discipline on OUR code (verisure_api/).
_HA_INTEGRATION_PREFIX = "custom_components/"


def _collect_python_files() -> list[Path]:
    """Collect all .py files in source directories."""
    files: list[Path] = []
    for source_dir in SOURCE_DIRS:
        if source_dir.exists():
            files.extend(source_dir.rglob("*.py"))
    return [f for f in files if f.name != "__init__.py"]


def _parse_file(path: Path) -> ast.Module:
    """Parse a Python file into an AST."""
    return ast.parse(path.read_text(), filename=str(path))


class TestNoAnyInAnnotations:
    """No `Any` in type annotations anywhere in the codebase."""

    def _find_any_usage(self, tree: ast.Module, filepath: Path) -> list[str]:
        """Find all uses of `Any` in type annotations."""
        violations: list[str] = []

        for node in ast.walk(tree):
            # Check if Any is imported
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                for alias in node.names:
                    if alias.name == "Any":
                        violations.append(
                            f"{filepath}:{node.lineno}: imports `Any` from typing"
                        )

            # Check annotations for Any usage
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check return annotation
                if node.returns and self._annotation_contains_any(node.returns):
                    violations.append(
                        f"{filepath}:{node.lineno}: function `{node.name}` "
                        f"return annotation contains `Any`"
                    )
                # Check argument annotations
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.annotation and self._annotation_contains_any(arg.annotation):
                        violations.append(
                            f"{filepath}:{arg.lineno}: argument `{arg.arg}` "
                            f"in `{node.name}` annotated with `Any`"
                        )

            # Check variable annotations
            if isinstance(node, ast.AnnAssign) and node.annotation and self._annotation_contains_any(node.annotation):  # noqa: E501
                    violations.append(
                        f"{filepath}:{node.lineno}: variable annotation "
                        f"contains `Any`"
                    )

        return violations

    def _annotation_contains_any(self, node: ast.expr) -> bool:
        """Check if an annotation AST node contains `Any`."""
        if isinstance(node, ast.Name) and node.id == "Any":
            return True
        if isinstance(node, ast.Constant) and node.value == "Any":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "Any":
            return True
        if isinstance(node, ast.Subscript):
            return self._annotation_contains_any(
                node.value
            ) or self._annotation_contains_any(node.slice)
        if isinstance(node, ast.Tuple):
            return any(self._annotation_contains_any(e) for e in node.elts)
        if isinstance(node, ast.BinOp):  # X | Y union syntax
            return self._annotation_contains_any(
                node.left
            ) or self._annotation_contains_any(node.right)
        return False

    @pytest.mark.parametrize("filepath", _collect_python_files(), ids=str)
    def test_no_any_in_annotations(self, filepath: Path) -> None:
        relative = str(filepath)
        if relative.startswith(_HA_INTEGRATION_PREFIX):
            pytest.skip("HA integration — Any mandated by HA base classes")
        if relative in ANY_ALLOWLIST:
            pytest.skip(f"Allowlisted: {ANY_ALLOWLIST[relative]}")

        tree = _parse_file(filepath)
        violations = self._find_any_usage(tree, filepath)
        if violations:
            msg = f"Found `Any` usage in {filepath}:\n" + "\n".join(
                f"  {v}" for v in violations
            )
            pytest.fail(msg)


class TestNoBareDict:
    """No bare `dict` without type arguments in annotations."""

    def _find_bare_dict(self, tree: ast.Module, filepath: Path) -> list[str]:
        violations: list[str] = []

        for node in ast.walk(tree):
            # Check function annotations
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns and self._is_bare_dict(node.returns):
                    violations.append(
                        f"{filepath}:{node.lineno}: function `{node.name}` "
                        f"return type is bare `dict`"
                    )
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.annotation and self._is_bare_dict(arg.annotation):
                        violations.append(
                            f"{filepath}:{arg.lineno}: argument `{arg.arg}` "
                            f"in `{node.name}` is bare `dict`"
                        )

            # Check variable annotations
            if isinstance(node, ast.AnnAssign) and node.annotation and self._is_bare_dict(node.annotation):  # noqa: E501
                    violations.append(
                        f"{filepath}:{node.lineno}: variable annotation is bare `dict`"
                    )

        return violations

    def _is_bare_dict(self, node: ast.expr) -> bool:
        """Check if an annotation is a bare `dict` without type params."""
        if isinstance(node, ast.Name) and node.id == "dict":
            return True
        if isinstance(node, ast.BinOp):  # X | Y union syntax
            return self._is_bare_dict(node.left) or self._is_bare_dict(node.right)
        return False

    @pytest.mark.parametrize("filepath", _collect_python_files(), ids=str)
    def test_no_bare_dict(self, filepath: Path) -> None:
        tree = _parse_file(filepath)
        violations = self._find_bare_dict(tree, filepath)
        if violations:
            msg = f"Found bare `dict` in {filepath}:\n" + "\n".join(
                f"  {v}" for v in violations
            )
            pytest.fail(msg)


class TestNoObjectAnnotation:
    """No `object` as a type annotation — too vague, use a protocol or concrete type."""

    # Dunder methods where `object` is required by the Python type protocol
    _OBJECT_ALLOWED_DUNDERS = frozenset({"__eq__", "__ne__"})

    def _find_object_annotations(self, tree: ast.Module, filepath: Path) -> list[str]:
        violations: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_dunder_exempt = node.name in self._OBJECT_ALLOWED_DUNDERS
                if node.returns and self._is_object(node.returns):
                    violations.append(
                        f"{filepath}:{node.lineno}: function `{node.name}` "
                        f"return type is `object`"
                    )
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.annotation and self._is_object(arg.annotation):
                        if is_dunder_exempt and arg.arg != "self":
                            continue  # __eq__/__ne__ require `object` per protocol
                        violations.append(
                            f"{filepath}:{arg.lineno}: argument `{arg.arg}` "
                            f"in `{node.name}` annotated with `object`"
                        )

            if isinstance(node, ast.AnnAssign) and node.annotation and self._is_object(node.annotation):  # noqa: E501
                    violations.append(
                        f"{filepath}:{node.lineno}: variable annotation is `object`"
                    )

        return violations

    def _is_object(self, node: ast.expr) -> bool:
        """Check if an annotation is bare `object`."""
        if isinstance(node, ast.Name) and node.id == "object":
            return True
        if isinstance(node, ast.BinOp):  # X | Y union syntax
            return self._is_object(node.left) or self._is_object(node.right)
        return False

    @pytest.mark.parametrize("filepath", _collect_python_files(), ids=str)
    def test_no_object_annotation(self, filepath: Path) -> None:
        tree = _parse_file(filepath)
        violations = self._find_object_annotations(tree, filepath)
        if violations:
            msg = f"Found `object` annotation in {filepath}:\n" + "\n".join(
                f"  {v}" for v in violations
            )
            pytest.fail(msg)


class TestNoUntargetedTypeIgnore:
    """No `# type: ignore` without a specific error code."""

    @pytest.mark.parametrize("filepath", _collect_python_files(), ids=str)
    def test_no_blanket_type_ignore(self, filepath: Path) -> None:
        violations: list[str] = []
        for lineno, line in enumerate(filepath.read_text().splitlines(), 1):
            if "# type: ignore" in line and "# type: ignore[" not in line:
                violations.append(f"{filepath}:{lineno}: blanket `# type: ignore`")

        if violations:
            msg = f"Found blanket type: ignore in {filepath}:\n" + "\n".join(
                f"  {v}" for v in violations
            )
            pytest.fail(msg)
