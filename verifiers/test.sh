#!/bin/bash
# Grade one attempt. /app is the model's tree; /verifier is read-only.
set -euo pipefail

# 1. Copy only the solution's source into a clean checkout (drops any
#    conftest.py, pytest.ini, sitecustomize.py, .pth, etc. the model added).
CLEAN=$(mktemp -d)
git -C /app add -A -- huey docs
git -C /app diff --cached --binary 5aba8d35a2504a35be46d39734d02eebb1471c62 -- huey docs > /tmp/solution.patch
git clone -q /app "$CLEAN/huey" && git -C "$CLEAN/huey" checkout -q 5aba8d35a2504a35be46d39734d02eebb1471c62
git -C "$CLEAN/huey" apply --allow-empty /tmp/solution.patch
find "$CLEAN/huey" \( -name conftest.py -o -name sitecustomize.py -o -name '*.pth' \) -not -path '*/huey/tests/*' -delete
/opt/venv/bin/pip install -q --no-deps --no-build-isolation "$CLEAN/huey"

# 2. Services.
redis-server --port 6379 --save '' --appendonly no --daemonize yes
for i in $(seq 50); do redis-cli ping >/dev/null 2>&1 && break; sleep 0.1; done

# 3. huey's own suite must still pass (V1, second half).
(cd "$CLEAN/huey" && env -i PATH=/usr/bin:/bin /opt/venv/bin/python -I runtests.py)

# 4. Hidden checks, isolated from anything in /app.
cd /tmp
env -i PATH=/usr/bin:/bin HOME=/tmp \
  SOLUTION_PYTHON=/opt/venv/bin/python LEGACY_PYTHON=/opt/huey354/bin/python \
  VERIFIER_SEED="${VERIFIER_SEED:-1337}" \
  /opt/venv/bin/python -m pytest -c /verifier/pytest.ini --rootdir /verifier \
    --confcutdir /verifier -p no:cacheprovider --junitxml=/tmp/r.xml /verifier/tests || true

# 5. Pass only if every expected test id ran and passed.
/opt/venv/bin/python -I - <<'PY'
import sys, xml.etree.ElementTree as ET
expected = {
    'test_basics': 2 * 4, 'test_redelivery': 4 * 4 + 2, 'test_soak': 4,
    'test_redeliveries_and_locks': 4 * 4, 'test_schedule_upgrade_perf': 3 * 4 + 1,
}
seen = {}
for case in ET.parse('/tmp/r.xml').iter('testcase'):
    if any(c.tag in ('failure', 'error', 'skipped') for c in case):
        print('FAILED', case.get('classname'), case.get('name')); sys.exit(1)
    mod = case.get('classname').split('.')[-1]
    seen[mod] = seen.get(mod, 0) + 1
if seen != expected:
    print('unexpected test set', seen); sys.exit(1)
print('all checks passed')
PY
