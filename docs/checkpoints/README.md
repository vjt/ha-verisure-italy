# Checkpoints

Session log for ha-verisure. Each checkpoint groups a handful of
sessions (typically 5–8) before rotating to a new file.

Rules:
- Exactly one checkpoint has `status: active` in its frontmatter.
- Sessions are added under `## Sn: Title (YYYY-MM-DD)` headings by
  the `/close` skill at the end of a working session.
- Rotate when the active checkpoint exceeds ~8 sessions or ~200
  lines. The old file flips to `status: done`; a new `cpNN.md` opens
  with `status: active` and a `Previous:` summary.

The first checkpoint (CP01) starts empty and gets sections appended
as sessions close.
