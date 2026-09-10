# Contributing Fire Drills

Contribute a small, reproducible synthetic workflow failure. A case must state its fixture, bounded trigger, independent outcome assertions, allowed escalation, provenance, limitations and version. Keep money in integer minor units with currency.

1. Add a strict JSON manifest based on an implemented case. Packs are data: no expressions, imports, shell commands or executable assertions.
2. Add a deliberately faulty control which actually performs the mistake and an appropriate corrected control. Neither may inspect the case identifier, fault schedule or oracle.
3. Execute both against an isolated simulator and include the resulting checks. Test an ordinary control so the oracle's false positives are visible.
4. Document supported adapter capabilities and what the model omits. Unimplemented event behavior must remain unsupported. Do not call synthetic failures real incidents or imply provider conformance.
5. Record a version change when fixtures, contracts or assertions change; do not silently rewrite old evidence.

Only contribute material you have the right to share. Private customer cases stay private. A sanitized reproduction requires explicit customer review and separate permission before publication.

Run the commands in the current README and inspect STATUS.md for milestone gates before proposing changes. Public publishing and license adoption require owner approval.
