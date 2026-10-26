#!/bin/bash
# Frontend-only adapter for the existing repository. No business DB or model calls.
set -uo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
result=0
run_check() {
  echo "CHECK: $*"
  if "$@"; then echo "PASS"; else echo "FAIL"; result=1; fi
}
cd "$project_dir/frontend" || exit 1
run_check npm run typecheck
run_check npm run lint
run_check npm test
run_check npm run build
cd "$project_dir" || exit 1
run_check git diff --check
run_check python3 - <<'PY_SCAN'
import re, subprocess
from pathlib import Path
root=Path.cwd()
tracked=subprocess.check_output(['git','ls-files','-z'],text=True).split('\0')
for name in tracked:
    if name and Path(name).name in {'.env','.env.local','.env.production'}:
        raise SystemExit('FAIL: runtime env tracked; filename only: '+name)
patterns=[rb'sk-[A-Za-z0-9_-]{20,}',rb'AKIA[0-9A-Z]{16}',rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']
files=[p for folder in ['frontend/src','frontend/dist'] for p in (root/folder).rglob('*') if p.is_file()]
for path in files:
    if any(re.search(pattern,path.read_bytes()) for pattern in patterns):
        raise SystemExit('FAIL: possible secret pattern; filename only: '+str(path.relative_to(root)))
print('PASS: no runtime env tracked; no selected credential patterns in frontend source/build. Pattern scan is not a complete security audit.')
PY_SCAN
echo "INTEGRATED: project-scoped director conversation/receipts and server canvas position/viewport persistence."
echo "PENDING: real-provider end-to-end generation acceptance for this revision; full canvas node generation-config/asset-binding persistence; final human review."
echo "Offline checks do not replace browser acceptance or real-model evaluation, and do not declare internal-test readiness."
exit "$result"
