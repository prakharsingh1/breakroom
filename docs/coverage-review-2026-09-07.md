# Coverage applicability review — 2026-09-07

The first five-case mutation review executed 12 trials and found 5 of 7 declared detections, with 2 incomplete negative observations. All 5 corrected controls passed. The review correctly exited 2. The underlying evidence remains in `artifacts/coverage-initial-five`; its reports and frozen manifest hashes were not rewritten.

The incomplete pairs were `refund-response-lost → success_without_evidence` and `refund-before-commit → always_escalate`. Both faulty implementations produced independently evaluated FAIL results. Neither exercised its case's required payment fault, so those failures did not establish the declared fault coverage. Their intended general regressions remain valuable: unsupported completion claims and unconditional escalation must still fail relevant ordinary completion checks.

The revised applicability mapping retains `faulty` as the exercise-aware lost-response control. The before-commit case now declares `no_retry_after_transient`, a control that actually makes the initial payment attempt, observes the transient failure, then escalates instead of completing the required bounded retry. The generic `always_escalate` and `success_without_evidence` behaviors and independent regression tests remain. No fault-coverage or business-outcome assertion was weakened.

Both affected cases were versioned from `1.0.0` to `1.0.1`, and the pack from `1.0.0` to `1.1.0`. Saved reports preserve their old case and pack metadata. Cross-version comparison must surface those incompatibilities.

| Manifest | Original SHA-256 | Reviewed SHA-256 |
|---|---|---|
| `refund-response-lost` | `683a9330bb2d95408eaf9b9c95b097eb60a3e582cab0874a3c31040a89b7842d` | `4c8acef58f7883cc0074d7767c845ff4f62d0d39b3df8410265d14d327fddd1d` |
| `refund-before-commit` | `1994da0220da64efc070c26e2b07e64ba642ce143338c22de3b1187b2b9bb711` | `9e884fae106d47dea6834b954b20d004c15ffcdfd847747e72a6fb008e922606` |

After the 24 case implementations and revised mapping were available, this command executed successfully:

```sh
python -m breakroom mutation-check --pack support-refunds --out artifacts/coverage-24
```

The saved result contains 53 actual subprocess trials: 29 of 29 declared negative-control detections and 24 of 24 corrected PASS results, with no incomplete or missed required observations, mutation gate exit 0. The first full matrix used the only then-supported fixture seed, 0. Repeated trials and subsequent variation evidence are recorded separately; the canonical result does not imply those additional trials ran. Later manifest/generator changes produce new hashes and new evidence; the hashes above identify this specific review snapshot.

This review checks test-pack detection under synthetic conditions. It does not establish provider compatibility, commercial-model performance, a customer agent release gate, or completion of every Cut B acceptance requirement by itself.

## Repeat and variation verification

The canonical seed-0 repeat run executed 159 fresh trials and exited 0: 87/87 declared negative detections and 72/72 corrected PASS results. Evidence is retained at `artifacts/coverage-24-repeated`.

After the bounded generator and reviewed seeds 0, 1 and 2 were implemented, this command also exited 0:

```sh
python -m breakroom mutation-check --pack support-refunds --seed 0 --seed 1 --seed 2 --trials 3 --out artifacts/coverage-24-variations
```

It executed 477 new subprocess trials across all 24 cases, three supported fixture seeds, and three independent repeats of each scheduled control. The actual saved result contains 261/261 declared negative detections and 216/216 corrected PASS results, with zero incomplete or missed required observations. Unmapped case/control combinations are explicitly unscheduled and do not inflate these counts.

Each generated report retains its canonical source manifest/hash, generator version, validated materialized manifest/hash, actual fixture hash and seed. The matrix verifies those identities and links each cell to a distinct recorded trial. The generation review changed source hashes after the earlier applicability snapshot; the older artifacts and hashes above remain historical evidence and were not rewritten.

Sixteen coverage contract tests passed using `python -m unittest tests.contract.test_coverage -q`. They include real repeated/varied trials, JSON/HTML/JUnit compatibility, executable CLI output, strict missing/unknown/unsupported/untriggered handling, detection-gate precedence, unchanged denominators, source/fixture identity, matrix-tampering rejection and blocked manifest-controlled imports.
