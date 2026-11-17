"""Every version string in the repo must match pyproject.toml.

`verisure_italy.__version__` feeds HA diagnostics and the CLI
`--version`; it sat at 0.9.6 through three minor releases because the
release checklist only covered pyproject.toml and manifest.json.
"""

import json
import tomllib
from pathlib import Path

import verisure_italy

MANIFEST = Path("custom_components/verisure_italy/manifest.json")


def _pyproject_version() -> str:
    with Path("pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_package_version_matches_pyproject() -> None:
    assert verisure_italy.__version__ == _pyproject_version()


def test_manifest_version_and_requirement_match_pyproject() -> None:
    manifest = json.loads(MANIFEST.read_text())
    version = _pyproject_version()
    assert manifest["version"] == version
    assert manifest["requirements"] == [f"verisure-italy>={version}"]
