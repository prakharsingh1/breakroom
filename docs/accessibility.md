# Accessibility verification

Breakroom uses semantic headings, named controls, text verdicts alongside color, visible focus outlines, a working skip-to-content link, and reduced-motion styles. The team navigation wraps on narrow screens so settings remain reachable. No remote font or image is required.

The browser suite uses pinned `@axe-core/playwright` 4.13.0 with WCAG 2 A/AA, WCAG 2.1 A/AA, WCAG 2.2 AA and best-practice tags. No rule is disabled and no page region is excluded. It checks home, library, a local drill, docs, pricing, empty Runs/Compare, customer signup and account access, the private dashboard, project overview, guided agent connection, release suite builder and readiness, newly executed demo evidence, a real comparison, saved evidence, private report/project/key views, and billing states. Invalid verification/reset links and honest recovery availability are also covered. Responsive pages are checked at 390, 768 and 1440 pixels. Billing UI fixtures are separate from the real team journey and do not claim a provider transaction happened.

The scan found and the implementation repaired insufficient contrast in secondary labels, skipped heading levels, a named reference-selection container without a group role, and a skip link whose target could not receive focus. The customer dashboard and project overview report summaries now also have explicit group roles for their accessible names. Visual review identified and repaired missing horizontal padding in the dashboard's empty report card. Browser tests exercise actual keyboard focus transfer and reduced motion and assert no page-level horizontal overflow.

The customer gate on 2026-09-10 passed **14 browser tests in 2.7 minutes**, with **53 accessibility scans reporting zero violations**. All 12 new customer screenshots—signup, dashboard, overview and suites, each at 390, 768 and 1440 pixels—were visually reviewed for clipping, legibility, hierarchy and usable controls. Screenshots of the actual demo, private team project and explicitly labeled billing test states were also inspected at the three widths. After the final group-role and empty-card-padding fixes, all 3 focused account/workspace tests passed in 56.8 seconds. The refreshed scan results retain zero violations, with only 15 decorative color-contrast entries left for manual review; the fixed mobile dashboard was inspected again. See [STATUS.md](../STATUS.md) for the latest executed gate and follow-up results.

Run from the repository root with the full local stack started and the `venv-team` dependencies installed as described in [DELIVERY.md](../DELIVERY.md). The [browser-test overlay](../infra/compose.e2e.yaml) accommodates hundreds of requests through one local proxy. It leaves password-abuse budgets unchanged; restore normal request limits after the run:

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml -f infra/compose.e2e.yaml up -d --no-deps --wait team-api
PYTHONPATH="$PWD/packages/breakroom-core/src" BREAKROOM_TEST_PYTHON="$PWD/venv-team/bin/python" BREAKROOM_TEAM_E2E=1 npm --prefix apps/web run test:e2e
docker compose -f infra/compose.yaml -f infra/compose.team.yaml up -d --no-deps --wait team-api
```

Install the matching Chromium with `cd apps/web && npx playwright install chromium` if it is absent. On the verified Mac, `BREAKROOM_BROWSER_EXECUTABLE` selects an already installed Chromium 151 because downloading the matching Chromium 153 timed out; the exact override is in [DELIVERY.md](../DELIVERY.md). The [CI workflow](../.github/workflows/ci.yml) installs the matching browser and runs the public and customer journeys. It also runs in [GitHub Actions](https://github.com/prakharsingh1/breakroom/actions); the local results above are recorded separately from remote run results.

Each scan writes bounded results to `artifacts/accessibility/`. `needs_manual_review` is retained, not counted as a pass. Remaining contrast-review entries identify decorative arrows and empty-state glyphs; their meaning is also stated in readable text and verdict labels. These glyphs were reviewed in the screenshots and source. The final executed counts and exit codes are recorded in `STATUS.md`.

Automated checks and this keyboard/visual review are a bounded accessibility pass, not certification or comprehensive assistive-technology testing. Real screen-reader testing with users remains useful before a public release. The checker uses the [official AxeBuilder API](https://github.com/dequelabs/axe-core-npm/tree/develop/packages/playwright).

2026-09-11 sandbox extension: full 15-test browser gate passed locally; four new sandbox scans increase the retained scan count to 57, with zero violations. New mobile/tablet/desktop introduction and dark evidence panels were visually inspected. The real ZIP upload/execution/evidence journey also passed in the separate Linux gVisor CI job. Scope remains a bounded review, not a certification.
