---
name: verisure-deploy
description: Full deploy pipeline — test, typecheck, deploy to HAOS, smoke test
---

Run the full deployment pipeline. Abort on any failure — never continue past a red gate.

## Steps

1. **Test suite**: `pytest tests/ -x -q` — abort if any test fails.
2. **Type check**: `pyright verisure_italy/ custom_components/` — abort if any new errors.
3. **Lint**: `ruff check verisure_italy/ tests/ custom_components/` — abort on errors.
4. **Deploy files**: push all `custom_components/verisure_italy/*.py` to HAOS via SSH pipe:
   ```bash
   for f in custom_components/verisure_italy/*.py; do
     ssh root@homeassistant -p 22222 \
       "cat > /mnt/data/supervisor/homeassistant/custom_components/verisure_italy/$(basename $f)" \
       < "$f"
   done
   ```
5. **Restart HA**: `ssh root@homeassistant -p 22222 "ha core restart"`
6. **Wait**: 30 seconds for HA to fully start.
7. **Smoke test**: `./scripts/smoke_test.sh` — abort if any entity/service missing.
8. **Log check**: `ssh root@homeassistant -p 22222 "docker logs homeassistant 2>&1 | grep -i verisure_italy | tail -20"` — look for ERROR/WARNING.

On failure at any step: stop, show full error, do NOT proceed or auto-fix.
On success: report all gates passed.

**Deploy does NOT release.** Releasing (PyPI + GitHub tag) is a separate manual step.
