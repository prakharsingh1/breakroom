# Executed mutation coverage

`breakroom mutation-check` tests whether a Fire Drill detects the mistakes its manifest declares. It executes the conservative corrected reference and each relevant deliberately faulty reference through the normal isolated runner. It reads each trial's independent checks and actual effects; a control name never chooses a verdict.

From the repository root, after installing the local core:

```sh
breakroom mutation-check --pack support-refunds --out artifacts/coverage
breakroom mutation-check --case refund-response-lost --trials 3 --out artifacts/coverage-repeated
breakroom mutation-check --pack support-refunds --seed 0 --seed 1 --seed 2 --trials 3 --out artifacts/coverage-24-variations
```

Every declared negative-control trial remains in the denominator. A detection requires a known applicable failed safety, outcome, evidence or liveness check and complete required fault/behavior coverage. Missing code, a worker infrastructure error, unsupported capabilities, and untriggered faults produce incomplete observations. Actual budget/liveness violations can remain detected even when the runner terminates the agent. An ordinary crash alone cannot count as a successful detection.

`matrix.json` shows every case/control/seed/repeat cell. The default executes declared pairs plus corrected controls; unmapped pairs are explicitly `not_scheduled`. No behavior is inferred for them. Add `--all-pairs` to a selected case subset to execute additional combinations. The same 500-execution bound applies, so a large full cross product must be divided into smaller case selections. The denominator remains the manifest's explicit required mapping; extra failures do not inflate it.

`--seed` can be repeated for supported fixture variations, and `--trials` requests 1–20 new isolated runs per pair. Seeds not supported by any selected manifest are rejected before execution. Identical seeds do not make external model behavior deterministic. The built-in scripted references use no model/provider account and receive no inherited provider secrets.

The current 24-case pack supports reviewed seeds 0, 1 and 2. The last command above executed 477 fresh trials on 2026-09-07: 261/261 declared negative detections and 216/216 corrected PASS results, with zero incomplete or missed required observations. See the [dated review record](coverage-review-2026-09-07.md) and saved artifacts for the exact scope. These are calculated results of the scripted control suite, not a commercial-model score.

The output includes:

- `matrix.json`: case/pack versions and source hashes, materialized manifest/fixture hashes and generator version per executed cell, the expected mapping, actual verdicts, coverage status, failed checks, missing observations, counts and an explicit numerator/denominator.
- `reports/report.json`: complete versioned run bundles; each matrix cell points to its actual report index and run ID.
- `reports/report.html` and `reports/junit.xml`: existing escaped/report-compatible evidence formats.
- `README.md`: calculated counts and scope.

The mutation-review exit code has a different purpose from a customer release gate. Exit 0 means corrected controls passed and all declared mistakes were detected under the tested conditions. Exit 1 means a required mutant actually passed, or a corrected control had a known failure. Exit 2 means incomplete required evidence, unsupported/missing controls, missing mappings, invalid configuration or no observations, unless a known exit-1 condition also exists. The saved ordinary evidence bundle contains intentional failing runs and consequently has a failing ordinary release gate. A successful mutation review never labels those faulty agents safe.

Manifests can name only entries resolved through the maintained builtin control registry. Unknown labels are recorded as incomplete without importing any manifest-provided source, path or module reference. A custom case remains bounded JSON data. Add new control behavior as reviewed local Python source, with its own test, rather than turning a manifest into executable code.

Review and version changes to a declared mapping. If a control never reaches the intended failure condition, retain its independent regression where useful, correct the applicability mapping with a documented case/pack version change, and rerun the corrected and appropriate faulty controls. Do not mark untriggered faults as successful coverage or rewrite old saved reports.

Use the [scenario contribution workflow](scenario-contribution.md) and [compatibility record](compatibility.md) when proposing new cases. An executed matrix is evidence about these synthetic workflow mistakes, not a benchmark of commercial models, provider certification, statistical significance, or a guarantee of general agent safety.
