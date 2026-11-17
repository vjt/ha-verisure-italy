"""Translation files must match strings.json.

Custom integrations read `translations/<lang>.json` at runtime;
`strings.json` is only the development source. A step or abort reason
present in `strings.json` but missing from `translations/en.json`
renders without title or field labels (HACS review of PR #13517
caught exactly this for the reauth steps).
"""

import json
from pathlib import Path

INTEGRATION_DIR = Path("custom_components/verisure_italy")


def test_en_json_matches_strings_json() -> None:
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    en = json.loads((INTEGRATION_DIR / "translations" / "en.json").read_text())
    assert en == strings
