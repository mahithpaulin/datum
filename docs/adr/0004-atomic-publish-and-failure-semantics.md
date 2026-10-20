# ADR 0004: Atomic publish and explicit failure destinations

- Status: Accepted
- Date: 2026-10-20

## Context

Replacing a working dataset with a partially written or quality-failed build is worse than
failing the build. Operators need a clear distinction between an invalid build and a build
that is structurally valid but fails policy thresholds.

## Decision

Create hidden staging and spool directories inside `output.dir`.

- On success, rename staging to `current/`; move an existing `current/` to `previous/`.
- On quality-threshold failure, rename staging to `failed/<build_id>-<timestamp>/` and leave
  `current/` unchanged.
- On any other failure, remove staging and leave published output untouched.
- Treat critical accounting invariant failures as build errors, not quality failures.
- Use exit codes: `0` success, `1` operational/verification failure, `2` quality failure,
  `3` usage/configuration failure, `130` cancellation.

Publishing assumes staging and destination are on the same filesystem. Staging is created
inside the output root to enforce this.

## Consequences

Positive:

- Readers never observe a partially written `current/`.
- Failed builds remain available for diagnosis.
- CI can distinguish quality policy failures from crashes.

Negative:

- Output directories require temporary disk space roughly equal to the build.
- `failed/` grows until cleaned.
- Rename semantics differ on unusual filesystems.

## Follow-up

Add retention policy and crash cleanup. Never publish by copying files individually into
`current/`.
