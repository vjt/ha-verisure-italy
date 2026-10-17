# Phantom disarm — `xSStatus` reports "D" while the panel stays armed

Observed 2026-10-16/17 on the maintainer's SDVECU, captured with
`verisure_italy.client` DEBUG logging on 2026-10-17 (v0.10.0).

## Symptom

HA flips `armed_away → disarmed → armed_away` within one or two polls.
The Verisure app shows the panel never left total mode. Seen at
2026-10-16 02:29, 2026-10-17 09:39, 15:24 and 21:07 (local time).

Downstream damage (pre-v0.11.0): "disarmed" push, cameras following
the alarm drop to standby, the away-safety-net automation re-arms. The
panel rejects that arm with `106 / ARM_STATUS_CHANGED` (it is already
armed), which also adds a failed-arm entry to the Verisure timeline.

## Capture

Phantom, 21:07 (response bodies as logged, redacted):

```
21:07:08.399 xSStatus {"status": "A", "timestampUpdate": "1792252634012", "exceptions": null}   (15:57:14.012Z)
21:07:13.391 xSStatus {"status": "D", "timestampUpdate": "1792264029172", "exceptions": null}   (19:07:09.172Z)
21:07:23.374 xSStatus {"status": "A", "timestampUpdate": "1792264033514", "exceptions": null}   (19:07:13.514Z)
```

The cloud recorded D at 19:07:09.172Z and A again at 19:07:13.514Z:
**4.342 s**, with no command from anyone. The A record predates the
safety net's `xSArmPanel` (19:07:13.867Z).

Real disarm, 21:40 (issued by HA, then left alone):

```
21:40:10.747 xSDisarmStatus {"res": "OK", "protomResponse": "D", "protomResponseDate": "2026-10-17T19:40:10Z", ...}
21:40:10.877 xSStatus {"status": "D", "timestampUpdate": "1792266009978", "exceptions": null}   (19:40:09.978Z)
21:40:15.988 … 21:40:35.383  same D / same timestampUpdate, 6 polls
```

## Findings

1. **A single response cannot tell them apart.** Same shape, same
   fields, fresh `timestampUpdate` in both cases. No source or
   quality field in `xSStatus`.
2. **`timestampUpdate` is when the cloud recorded the state** (epoch
   ms). It matches the command's `protomResponseDate` on both the
   disarm (19:40:09.978Z vs 19:40:10Z) and the re-arm (19:40:36.894Z
   vs 19:40:37Z). It is not a cache age.
3. **Persistence is the discriminator.** A real D repeats with the
   same `timestampUpdate`; the phantom was replaced within 4.3 s by A
   with a newer `timestampUpdate`.

## Handling (v0.11.0)

`VerisureCoordinator._may_publish`: a polled D while the client last
tracked an armed state is held (last confirmed snapshot kept, client
`currentStatus` untouched) and re-polled after
`disarm_confirm_delay` seconds (option, default 5 = one poll). It is
published only if D comes back with the same `timestampUpdate`. Any
non-D reading discards it (logged as WARNING); a D with a new
`timestampUpdate` restarts confirmation. A D after our own disarm
command, or on the first refresh, needs no confirmation.

Cost: an externally-sourced real disarm (app, keypad) reaches HA one
confirmation delay later. Open question: phantom duration
distribution (one sample: 4.3 s). A phantom lasting longer than the
delay with an unchanged `timestampUpdate` would still be published.
