---
name: verisure-deploy
description: Full deploy pipeline — test, typecheck, deploy to HAOS, smoke test
---

Run the full deployment pipeline. Abort on any failure — never continue past a red gate.

## Steps

1. **Test suite**: `pytest tests/ -x -q` — abort if any test fails.
2. **Type check**: `pyright verisure_italy/ custom_components/` — abort if any new errors.
3. **Lint**: `ruff check verisure_italy/ tests/ custom_components/` — abort on errors.
4. **Deploy integration files**: push all integration Python files + `manifest.json` to HAOS via SSH pipe. Manifest is load-bearing — it carries the integration version + client library pin, so HACS and HA Dev Tools both show the deployed version correctly:
   ```bash
   for f in custom_components/verisure_italy/*.py custom_components/verisure_italy/manifest.json; do
     ssh root@homeassistant -p 22222 \
       "cat > /mnt/data/supervisor/homeassistant/custom_components/verisure_italy/$(basename $f)" \
       < "$f"
   done
   ```
5. **Deploy client library**: the `verisure_italy/` package is pip-installed inside the HA
   Docker container. Must deploy it separately — the integration imports it at runtime:
   ```bash
   for f in verisure_italy/*.py; do
     ssh root@homeassistant -p 22222 \
       "docker exec -i homeassistant sh -c 'cat > /usr/local/lib/python3.14/site-packages/verisure_italy/$(basename $f)'" \
       < "$f"
   done
   ```
   **Note**: The Python version path (3.14) may change with HA updates. If deploy fails
   with "No such file or directory", check the actual path with:
   `ssh root@homeassistant -p 22222 "docker exec homeassistant python3 -c \"import verisure_italy; print(verisure_italy.__file__)\""`
6. **Restart HA**: `ssh root@homeassistant -p 22222 "ha core restart"` — returns immediately once the restart is *scheduled*; core takes ~30-60s to come back up.
7. **Wait until HA is up**: do NOT hard-sleep. The Bash tool blocks long leading `sleep N` commands. `ha core info` returns info even while core is restarting, so poll the HTTP API instead — it only answers 200 once HA is fully booted:
   ```bash
   source .env
   until [ "$(curl -sf -o /dev/null -w '%{http_code}' \
     http://homeassistant:8123/api/ -H "Authorization: Bearer $HA_TOKEN")" = "200" ]; do
     sleep 3
   done
   ```
   Use this inside a plain Bash call (not Monitor) — it exits as soon as the API responds. You'll get a completion notification automatically.
8. **Smoke test**: `./scripts/smoke_test.sh` — abort if any entity/service missing.
9. **Log check**: `ssh root@homeassistant -p 22222 "docker logs homeassistant 2>&1 | grep -i verisure_italy | tail -20"` — look for ERROR/WARNING.

On failure at any step: stop, show full error, do NOT proceed or auto-fix.
On success: report all gates passed.

**Deploy does NOT release.** Releasing (PyPI + GitHub tag) is a separate manual step.

## Live E2E cycle — state and automation restoration (MANDATORY)

If an end-to-end arm/disarm cycle is run against the live panel as
part of verification (either within this skill or downstream in a plan
like `docs/plans/*.md`), the panel's **initial state** and the user's
**automations** must be restored afterwards. This is a real home
alarm — leaving it in the wrong state is a security failure.

Protocol:

1. **Capture initial state** before the first transition.
   ```bash
   source .env
   INITIAL=$(curl -s http://homeassistant:8123/api/states/alarm_control_panel.verisure_alarm \
     -H "Authorization: Bearer $HA_TOKEN" | python3 -c "import sys,json;print(json.load(sys.stdin)['state'])")
   echo "initial: $INITIAL"
   ```
2. **Pause automations** that would interfere with deliberate
   mutations (auto-disarm, morning-disarm, actionable-disarm notification, etc.). List is in auto-memory `project_ha_automations.md`.
3. **Cycle** through the planned transitions.
4. **Restore initial state.** If `INITIAL == armed_away`, the last
   transition must be `alarm_arm_away`, not `alarm_disarm`. Never
   end the cycle on a different state than where you started.
5. **Re-enable automations** paused in step 2.
6. **Verify**: `alarm_control_panel.verisure_alarm` state equals
   `INITIAL`, and every paused automation is back to `on`.

Do not rely on a plan's default "cycle ends disarmed" template —
patch the last transition to restore. If unsure what the initial
state was, ASK the user before running the cycle.
