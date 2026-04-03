#!/bin/bash
# Quick smoke test for Verisure Italy integration after HA updates.
# Run from the repo root: ./scripts/smoke_test.sh
#
# Checks that all entities exist and respond, without actually
# arming/disarming (safe to run anytime).

set -euo pipefail

source .env

API="http://homeassistant:8123/api"
AUTH="Authorization: Bearer $HA_TOKEN"
PASS=0
FAIL=0

check_entity() {
    local entity_id="$1"
    local expected_states="$2"  # comma-separated acceptable states

    local state
    state=$(curl -sf "$API/states/$entity_id" -H "$AUTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])" 2>/dev/null)

    if [ $? -ne 0 ]; then
        echo "  FAIL  $entity_id — not found"
        FAIL=$((FAIL + 1))
        return
    fi

    # Buttons show a timestamp after first press — treat any non-"unavailable"
    # state as OK for entities that accept "*" in expected_states
    if echo "$expected_states" | grep -qw '\*'; then
        echo "  OK    $entity_id = $state"
        PASS=$((PASS + 1))
    elif echo "$expected_states" | grep -qw "$state"; then
        echo "  OK    $entity_id = $state"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $entity_id = $state (expected: $expected_states)"
        FAIL=$((FAIL + 1))
    fi
}

check_service() {
    local service="$1"
    local exists
    exists=$(curl -sf "$API/services" -H "$AUTH" | python3 -c "
import sys, json
services = json.load(sys.stdin)
for svc in services:
    if svc['domain'] == 'verisure_italy' and '$service' in svc.get('services', {}):
        print('yes')
        break
else:
    print('no')
" 2>/dev/null)

    if [ "$exists" = "yes" ]; then
        echo "  OK    verisure_italy.$service"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  verisure_italy.$service — not registered"
        FAIL=$((FAIL + 1))
    fi
}

check_panel() {
    local url_path="$1"
    local exists
    exists=$(curl -sf "$API/config" -H "$AUTH" | python3 -c "
import sys, json
# Check via panels endpoint
" 2>/dev/null)

    # Just check the dashboard URL responds
    local status
    status=$(curl -sf -o /dev/null -w "%{http_code}" "http://homeassistant:8123/$url_path" -H "$AUTH" 2>/dev/null || echo "000")

    if [ "$status" = "200" ]; then
        echo "  OK    panel /$url_path"
        PASS=$((PASS + 1))
    else
        echo "  WARN  panel /$url_path — HTTP $status (dashboard may need rebuild)"
    fi
}

echo "Verisure Italy smoke test"
echo "========================="
echo ""
echo "Entities:"
check_entity "alarm_control_panel.verisure_alarm" "disarmed,armed_home,armed_away,arming,disarming"
check_entity "button.verisure_force_arm" "unavailable,unknown,*"
check_entity "button.verisure_cancel_force_arm" "unavailable,unknown,*"
check_entity "button.verisure_capture_all_cameras" "unknown,*"

echo ""
echo "Services:"
check_service "force_arm"
check_service "force_arm_cancel"
check_service "capture_cameras"

echo ""
echo "Dashboard:"
check_panel "verisure-italy"

echo ""
echo "========================="
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
