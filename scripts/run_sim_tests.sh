#!/usr/bin/env bash
# Simulation tests: runs the fault_suite scenario headless (Gazebo + Nav2 + all nodes) and fails
# if any expectation in the scenario fails. Usage: scripts/run_sim_tests.sh [scenario]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/env.sh"
SCENARIO="${1:-fault_suite}"
RUN="${SCENARIO}_$(date +%Y%m%d_%H%M%S)"
RESULTS="${ROOT}/runtime/results"
mkdir -p "${RESULTS}"
timeout 5400 ros2 launch uco_bringup warehouse.launch.py gui:=false rviz:=false dashboard:=false \
  scenario:="${SCENARIO}" exit_after_scenario:=true results_dir:="${RESULTS}" run_name:="${RUN}" \
  > "${RESULTS}/${RUN}.log" 2>&1
grep "\[SCENARIO\]" "${RESULTS}/${RUN}.log" | sed 's/.*\[SCENARIO\] //'
python3 "${ROOT}/scripts/plot_results.py" "${RESULTS}/${RUN}" > /dev/null 2>&1 || true
python3 - "${RESULTS}/${RUN}/scenario_report.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
checks = [s for s in r['steps'] if s['step'] in ('expect', 'wait_for')]
print(f"\n{r['scenario']}: {'PASSED' if r['passed'] else 'FAILED'} - {sum(s['ok'] for s in checks)}/{len(checks)} checks")
for s in r['steps']:
    if not s['ok']:
        print('  FAILED step', s['index'], s['step'], s.get('arg'), s.get('observed', s.get('error', '')))
sys.exit(0 if r['passed'] else 1)
PY
