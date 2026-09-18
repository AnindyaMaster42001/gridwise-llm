#!/usr/bin/env bash
# Smallest possible proof that a deployment is judge-ready.
#   bash scripts/smoke.sh https://your-service.example.com
set -uo pipefail
BASE="${1:-http://localhost:8000}"

echo "== GET ${BASE}/health"
curl -sS -m 10 -w '\nHTTP %{http_code}  %{time_total}s\n' "${BASE}/health"

echo
echo "== POST ${BASE}/optimize-energy (public sample 1)"
python3 - "$BASE" <<'PY'
import json, subprocess, sys
base = sys.argv[1]
pack = json.load(open("tests/data/public_samples.json"))
body = json.dumps(pack["cases"][0]["input"])
out = subprocess.run(
    ["curl", "-sS", "-m", "35", "-X", "POST", f"{base}/optimize-energy",
     "-H", "Content-Type: application/json", "-w", "\nHTTP %{http_code} %{time_total}s",
     "-d", body],
    capture_output=True, text=True,
)
print(out.stdout[-2000:])
print(out.stderr[-500:], file=sys.stderr)
PY

echo
echo "== POST ${BASE}/optimize-energy with malformed JSON (expect HTTP 400)"
curl -sS -m 10 -X POST "${BASE}/optimize-energy" \
  -H 'Content-Type: application/json' \
  -d '{"scenario_id": ' -w '\nHTTP %{http_code}\n'
