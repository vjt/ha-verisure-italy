# Camera Capture Tuning

Tuned 2026-04-04 on the live 6-camera SDVECU setup.

| Strategy        | Total time | Errors                                           |
|-----------------|------------|--------------------------------------------------|
| Sequential      | ~84 s      | 0 — ~14 s per camera                             |
| Parallel 0.5 s  | —          | API overwhelmed: 4/6 `alarm_process_error`, 1 lost all retries |
| Parallel 2 s    | ~37 s      | 0 — 6/6 success, no retries needed               |

The Verisure alarm panel cannot handle truly parallel capture
requests. A **2 s stagger between launches** is the sweet spot:
polite enough for the API, 2.3× faster than sequential.

Each camera retries up to 2 times with exponential backoff (3 s, then
6 s) on failure.
