# Contributing a Fire Drill

A useful Fire Drill is a small reproducible business failure with an independently checked outcome. Start from an existing JSON manifest and describe the behavior the new case adds. Keep customer names, contact references, money and incidents synthetic unless separate rights and consent have been recorded.

1. State the initial business records, authorized logical request, permitted outcome and the exact challenge trigger. Distinguish a refusal before commit from a response lost after commit. Keep money in integer minor units with explicit currency and valid ownership/remaining-balance constraints.
2. Select supported assertion and fault identifiers from the engine's registries. Add missing semantics as reviewed engine code with focused tests. Manifests contain bounded data; no Python expressions, imports, commands, executable assertions or arbitrary network targets.
3. Implement a minimal negative control that actually performs the relevant mistake. It must react to task/tool observations and must not inspect case IDs, upcoming fault schedules, ground-truth state or expected answers. Register audited builtin controls explicitly in `breakroom.coverage`.
4. Preserve the ordinary control and appropriate conservative corrected behavior. Execute both through the real runner, including supported variation seeds and repeated trials when concurrency or timing matters. Unknown evidence and untriggered challenges do not establish a detection.
5. Record exact commands, exit codes, report paths and review findings with [the review template](templates/fire-drill-review.md). Use the generated matrix's numerator/denominator; do not manufacture a percentage or remove incomplete required cells.
6. Version the case, pack, simulator/oracle contract and generator when their meaning changes. Document compatibility changes and deprecation guidance. Preserve immutable saved reports and exported manifests; reruns on changed contracts are new evidence.

For a private custom pack, work in a local directory outside the public pack and run:

```sh
breakroom validate-pack ./private-cases
breakroom run --agent your_package.adapter:run --pack ./private-cases --out ./artifacts/private-run
breakroom mutation-check --pack ./private-cases --out ./artifacts/private-coverage
```

These commands remain local and upload nothing. The customer adapter is trusted local executable Python; a subprocess is not a security sandbox and independently written customer code can access the host/network. The injected fake tools must receive no production payment/help-desk credentials. Unregistered private control labels are shown as incomplete; they are never dynamically imported from JSON.

Before proposing public inclusion, explicitly confirm rights to contribute each code/data/text component and any permission needed for a derived reproduction. Remove customer secrets and identify residual privacy limits. Do not assert that generated cases are real incidents, that a passing scenario certifies a commercial model, or that local simulation establishes provider conformance.
