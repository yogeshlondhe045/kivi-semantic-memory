#!/usr/bin/env bash
# Run the complete demonstration scenario (delivery -> storage -> processing request -> dispatch),
# record metrics and generate graphs.
#   scripts/run_demo.sh                 # with Gazebo GUI and RViz
#   scripts/run_demo.sh --headless      # no GUI windows (CI / remote machine)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/env.sh"
GUI=true; RVIZ=true
[[ "${1:-}" == "--headless" ]] && { GUI=false; RVIZ=false; }
# Isolate this run: stop leftovers of earlier runs (a Gazebo server can outlive its launch file
# because ros_gz_sim's wrapper does not forward SIGINT) and use a private gz-transport partition so
# a stray simulation can never publish into this one.
"${ROOT}/scripts/stop_all.sh"
export GZ_PARTITION="uco_$(date +%s)_$$"
trap '"${ROOT}/scripts/stop_all.sh"' EXIT
RUN="demo_$(date +%Y%m%d_%H%M%S)"
RESULTS="${ROOT}/runtime/results"
mkdir -p "${RESULTS}"
echo "Run: ${RUN}  (dashboard: http://localhost:8080)"
ros2 launch uco_bringup warehouse.launch.py gui:=${GUI} rviz:=${RVIZ} scenario:=demo exit_after_scenario:=true \
  results_dir:="${RESULTS}" run_name:="${RUN}" 2>&1 | tee "${RESULTS}/${RUN}.log"
python3 "${ROOT}/scripts/plot_results.py" "${RESULTS}/${RUN}" || true
if python3 -c "import json,sys; sys.exit(0 if json.load(open('${RESULTS}/${RUN}/scenario_report.json'))['passed'] else 1)"; then
  echo "DEMO PASSED - results in ${RESULTS}/${RUN}"
else
  echo "DEMO FAILED - see ${RESULTS}/${RUN}/scenario_report.json"; exit 1
fi
