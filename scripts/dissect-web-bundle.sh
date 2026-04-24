#!/usr/bin/env bash
# Dissect the Verisure IT web app bundle to extract panel types, arm/disarm
# command vocabulary, and the resolver logic used by the web client.
#
# The web app is an SPA at customers.verisure.it/owa-static — GraphQL enum
# values and command strings are embedded as JS string literals. Re-running
# this script on each bundle version detects schema drift before Verisure
# ships a breaking change to the API.
#
# Output: prints a summary to stdout. Full bundle + extracted fragments are
# left in $OUTDIR for manual inspection.
#
# Usage:
#     ./scripts/dissect-web-bundle.sh            # pin-less, probe for latest
#     ./scripts/dissect-web-bundle.sh 2.4.2       # pin a specific version
#
# Requires: curl, python3, grep.

set -euo pipefail

OUTDIR="${OUTDIR:-/tmp/verisure-web-bundle}"
BASE="https://customers.verisure.it"
VERSION="${1:-}"

mkdir -p "$OUTDIR"
cd "$OUTDIR"

if [[ -z "$VERSION" ]]; then
    # Parse the latest bundle version from the /owa-static landing page.
    echo "# probing latest bundle version from ${BASE}/owa-static/login ..." >&2
    HTML=$(curl -sL "${BASE}/owa-static/login")
    VERSION=$(echo "$HTML" | grep -oE '/[0-9]+\.[0-9]+\.[0-9]+/static/js/main\.[a-f0-9]+\.js' \
        | head -1 | cut -d/ -f2)
    if [[ -z "$VERSION" ]]; then
        echo "ERROR: could not detect bundle version from landing page" >&2
        exit 1
    fi
    echo "# detected version: $VERSION" >&2
fi

# Derive the main bundle URL from the landing page HTML (hash changes per build).
HTML=$(curl -sL "${BASE}/owa-static/login")
MAIN_URL=$(echo "$HTML" | grep -oE "/${VERSION}/static/js/main\.[a-f0-9]+\.js" | head -1)
if [[ -z "$MAIN_URL" ]]; then
    echo "ERROR: could not locate main bundle URL in landing HTML" >&2
    exit 1
fi

MAIN_FILE="main.${VERSION}.js"
echo "# fetching ${BASE}${MAIN_URL} -> ${OUTDIR}/${MAIN_FILE}" >&2
curl -sL "${BASE}${MAIN_URL}" -o "${MAIN_FILE}"

SIZE=$(wc -c < "${MAIN_FILE}")
echo "# fetched ${SIZE} bytes"
echo

# --- Extraction ---

echo "## Panel types"
python3 - "${MAIN_FILE}" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
# Panel enum: { SDVFAST:"SDVFAST", ... }
m = re.search(r'\{([^{}]*?:\s*"(SDVECU|SDVFAST|MODPRO)[^"]*"[^{}]*?)\}', src)
if m:
    print("  bundle object:", m.group(0))
panels = sorted(set(re.findall(r'"((?:SDV|MODPRO)[A-Z0-9-]*)"', src)))
print("  string literals:", panels)
# Family classification — the R(e) function returns true for peri-capable panels
mfam = re.search(
    r'=e=>\{switch\(e\)\{((?:case d(?:\.[A-Z]+|\["[A-Z-]+"\]):)+return!0;(?:case d(?:\.[A-Z]+|\["[A-Z-]+"\]):)+default:return!1\})',
    src,
)
if mfam:
    body = mfam.group(1)
    def parse_group(sub: str) -> list[str]:
        return re.findall(r'(?:d\.([A-Z]+)|d\["([A-Z-]+)"\])', sub)
    # Split at the first `return!0;` — everything before = family A
    true_side, _, false_side = body.partition('return!0;')
    family_a = [a or b for a, b in parse_group(true_side)]
    family_b = [a or b for a, b in parse_group(false_side)]
    print(f"  family A (peri-capable, R(e)=true):  {family_a}")
    print(f"  family B (no peri, R(e)=false):     {family_b}")
else:
    print("  (family classifier not found)")
PY
echo

echo "## ArmCodeRequest enum (wire values — end in digit, no underscores)"
grep -oE '"(ARM|PERI)[A-Z0-9]*[0-9]"' "${MAIN_FILE}" \
    | sort -u \
    | sed 's/^/  /'
echo

echo "## DisarmCodeRequest enum (wire values only)"
grep -oE '"DARM[A-Z0-9]*"' "${MAIN_FILE}" \
    | sort -u \
    | grep -E '^"DARM([0-9][A-Z0-9]*|PERI|ANNEX[0-9]?)?"$' \
    | sed 's/^/  /'
echo

echo "## Target-state → command resolver (decoded switch)"
python3 - "${MAIN_FILE}" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
# Anchor: the destructured-arg signature is very stable across builds.
m = re.search(
    r'=e=>\{let\{alarmMode:t,currentMode:i,isCU:a=!1\}=e;switch\(t\)\{([^}]*(?:\}[^}]*)*?)\}[^}]*\}',
    src,
)
if m:
    body = m.group(0)
    pretty = re.sub(r'(case n\.q2\.[A-Z_]+:)', r'\n    \1', body)
    pretty = re.sub(r'(default:)', r'\n    \1', pretty)
    print("  " + pretty[:2500])
else:
    print("  NOT FOUND — bundle layout shifted; search for destructured {alarmMode,currentMode,isCU} in the bundle")
PY
echo

echo "## Timeline query (ActV2Timeline — gql template literal)"
python3 - "${MAIN_FILE}" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
# Apollo `gql` template literal containing the query body.
m = re.search(r'query ActV2Timeline[\s\S]{50,3000}?xSActV2[\s\S]{50,3000}?(?=`)', src)
if m:
    print("  found @", m.start())
    print("  " + m.group(0)[:1800])
else:
    print("  raw query not found — likely inlined or stringified differently")
PY
echo

echo "## Done. Full bundle at: $OUTDIR/${MAIN_FILE}"
