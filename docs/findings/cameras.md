# Verisure Cameras — Hardware + API Findings

Captured 2026-04-03 against SDVECU panel.

## Hardware

- 6 cameras: 4×QR (indoor), 2×QP (perimeter).
- All produce 640×352 LOW-quality JPEGs (~28 KB).
- `get_photo_images` returns the same image as the thumbnail — no
  higher resolution available.
- Tested `resolution=0..3`, `mediaType=0..2`: all produce the same
  LOW quality. `mediaType=2` errors.

## Active vs passive capture

- `request_images` (`xSRequestImages`) is heavyweight: pings the
  panel, creates timeline entries, sends Verisure app push
  notifications.
- `get_thumbnail` (`xSGetThumbnail`) is passive: reads the CDN cache,
  no panel ping, no notification spam.
- Thumbnails only update after an active `request_images` capture —
  passive refresh alone is useless for fresh images.

## Design decisions

- No periodic capture timer (caused timeline spam + concurrent-request
  errors).
- Captures are on-demand only: `capture_cameras` service or per-camera
  button entity.
- Parallel captures with 2s stagger between launches (see
  [`camera-capture-tuning.md`](camera-capture-tuning.md)).
- Camera name + timestamp overlaid on images via Pillow (runs in
  executor).

## Undiscovered APIs

Operations present on the panel but whose GraphQL query names are
unknown: TIMELINE (service id 506), CONNSTATUS (509), DEACTIVATEZONE
(98).

Discovery attempts:
- Fuzzing `xS*` / `mkGet*` naming conventions: all returned HTTP 400.
- GraphQL introspection: disabled (HTTP 400).

**Recommended approach:** open `customers.verisure.it` in a browser
with DevTools, navigate to the relevant page (e.g. timeline), and
capture the real GraphQL queries from the Network tab. The web app
can't pin certs.
