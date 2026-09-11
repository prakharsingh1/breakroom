#!/usr/bin/env bash
# Only for the disposable GitHub Actions Linux runner; never a production installer.
set -euo pipefail
if [[ "${CI:-}" != "true" || "${RUNNER_OS:-}" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo 'This script requires a disposable Linux x86_64 CI runner.' >&2
  exit 2
fi
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
curl --fail --location --proto '=https' --tlsv1.2 --max-time 120 --output "$stage/gvisor.tar.bz2" \
  https://github.com/google/gvisor/releases/download/release-20260907.0/gvisor-x86_64.tar.bz2
(cd "$stage" && echo '81416511897ab8abd4e723d66823c5b0461a2ee3311cfa70d152404ef9b860cf  gvisor.tar.bz2' | sha256sum --check)
sudo tar -xjf "$stage/gvisor.tar.bz2" -C /usr/local/bin
sudo /usr/local/bin/runsc install
sudo systemctl reload docker
/usr/local/bin/runsc --version
docker info --format '{{json .Runtimes}}'
