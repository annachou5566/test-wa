# test-wa

Dedicated public test/cron runner repository for Wave Alpha.

## Purpose

Use `annachou5566/test-wa` for GitHub-hosted test jobs, scheduled cron checks, bounded QA probes, and temporary verification workflows needed while developing or validating Wave Alpha.

## Rules

- `annachou5566/wave-alpha` is private and must not consume GitHub-hosted Actions while the private-Actions ban is active.
- New Wave Alpha test/cron/QA workflows that genuinely need GitHub Actions belong here, not in `annachou5566/wave-alpha`.
- `annachou5566/data-fetcher-king` is reserved for its existing completed website-support workloads; do not add Wave Alpha development tests or QA cron jobs there.
- Do not copy private Wave Alpha source or secrets here merely to make a test run. Prefer public-safe probes, fixtures, or minimal test-only code.
- Use standard GitHub-hosted runners only, bounded requests/timeouts, and the minimum cadence needed for the test.
- This repository is a test execution surface only. It does not become a production data/feed/queue/archive/delivery owner for Wave Alpha.
- Remove or disable temporary test workflows after their purpose is complete so this repository stays readable.
