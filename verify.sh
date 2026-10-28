#!/usr/bin/env bash
# Existing-project adapter for the starter kit's five checks; stage modes stay separate.
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:-}"
if [[ "$mode" != "v0.11-backend" && "$mode" != "v0.11-frontend" && "$mode" != "canvas-core-backend" && "$mode" != "canvas-core-frontend" && "$mode" != "director-workflow-backend" && "$mode" != "director-workflow-frontend" && "$mode" != "frontend-closing" && "$mode" != "budget-reservation-fix" ]]; then
  echo 'Usage: APP_PYTHON=/absolute/python bash verify.sh v0.11-backend|v0.11-frontend|canvas-core-backend|canvas-core-frontend|director-workflow-backend|director-workflow-frontend|frontend-closing|budget-reservation-fix'
  exit 2
fi
app_python="${APP_PYTHON:-python3}"
if [[ "$mode" == "budget-reservation-fix" ]]; then
  evidence_dir="docs/evidence/v0.11-历史预算误拦截修复-20261028"
elif [[ "$mode" == "frontend-closing" ]]; then
  evidence_dir="docs/evidence/v0.11-前端收尾-20261028"
  export V011_WORKFLOW_DIST="/private/tmp/ecom-v011-frontend-preview/closing-dist"
elif [[ "$mode" == "director-workflow-frontend" ]]; then
  evidence_dir="docs/evidence/v0.11-对话多角色执行-前端-20261028"
elif [[ "$mode" == "director-workflow-backend" ]]; then
  evidence_dir="docs/evidence/v0.11-对话多角色执行-后端-20261027"
elif [[ "$mode" == "canvas-core-frontend" ]]; then
  evidence_dir="docs/evidence/v0.11-画布操作与节点执行-前端-20261027"
elif [[ "$mode" == "canvas-core-backend" ]]; then
  evidence_dir="docs/evidence/v0.11-节点执行与撤销-后端-20261027"
elif [[ "$mode" == "v0.11-frontend" ]]; then
  evidence_dir="docs/evidence/v0.11-切片1-前端-20261027"
else
  evidence_dir="docs/evidence/v0.11-切片1-后端-20261027"
fi
mkdir -p "$evidence_dir"
# Exclusive log path: verification never replaces a historical run.
run_stamp="$(TZ=Asia/Shanghai date +%Y%m%dT%H%M%S)-$$"
log_path="$evidence_dir/verify-$run_stamp.txt"
run_checks() {
echo "v0.11 backend verification: $run_stamp"
echo "1/5 Isolated backend regressions"
"$app_python" -m unittest discover -s backend/tests -v
"$app_python" scripts/check-v011-legacy.py
echo "2/5 Credential exclusion"
"$app_python" scripts/check-v011-files.py secrets
echo "3/5 Isolated service restart recovery"
V011_EVIDENCE_DIR="$evidence_dir/run-$run_stamp" "$app_python" scripts/check-v011-service-restart.py
echo "4/5 Frontend checks: N/A — frontend unchanged; no frontend completion claim"
echo "5/5 Required files and whitespace"
"$app_python" scripts/check-v011-files.py files
echo 'PASS: backend automated checks only; real LLM, browser and slice acceptance PENDING'
echo "Evidence: $log_path"
}

run_frontend_checks() {
  local run_dir="$evidence_dir/run-$run_stamp"
  local isolated_dist="/private/tmp/ecom-v011-frontend-preview/dist"
  mkdir -p "$run_dir"
  echo "v0.11 frontend automated verification: $run_stamp"
  echo 'Scope: current frontend source + isolated build; no live service restart, real model call or browser acceptance'
  "$app_python" scripts/check-v011-files.py snapshot-live-build "$run_dir/live-build-before.json"
  echo '1/5 Frontend lint, strict types and isolated production build'
  node --version
  npm --version
  npm --prefix frontend run lint
  npm --prefix frontend run typecheck
  npm --prefix frontend run build -- --outDir "$isolated_dist" --emptyOutDir
  echo '2/5 Full frontend test suite'
  npm --prefix frontend test -- --run
  echo '3/5 Credential exclusion, including the isolated build actually served'
  "$app_python" scripts/check-v011-files.py secrets-frontend
  echo '4/5 Independent temporary HTTP service restart and persistence recovery'
  V011_EVIDENCE_DIR="$run_dir" "$app_python" scripts/check-v011-service-restart.py
  echo '5/5 Required technical files, whitespace and unchanged live build'
  "$app_python" scripts/check-v011-files.py files-frontend
  "$app_python" scripts/check-v011-files.py check-live-build "$run_dir/live-build-before.json"
  echo 'PASS: frontend automated checks only; browser, real LLM, visual and slice acceptance remain separate'
  echo "Evidence: $log_path"
}

run_canvas_core_checks() {
  local run_dir="$evidence_dir/run-$run_stamp"
  mkdir -p "$run_dir"
  echo "Canvas execution and undo backend verification: $run_stamp"
  echo '1/5 Isolated backend tests and retained director regression'
  "$app_python" -m unittest discover -s backend/tests -v
  "$app_python" scripts/check-v011-legacy.py
  echo '2/5 Credential pattern exclusions'
  "$app_python" scripts/check-v011-files.py secrets
  echo '3/5 Actual isolated HTTP process restart; pending node task and undo history'
  CANVAS_CORE_EVIDENCE_DIR="$run_dir" "$app_python" scripts/check-canvas-core-restart.py
  echo '4/5 Frontend N/A: current batch edits backend and documents only'
  echo '5/5 Required technical files and whitespace'
  "$app_python" scripts/check-v011-files.py files-canvas-core
  echo 'PASS: backend implementation and isolated verification; real provider and frontend integration PENDING'
  echo "Evidence: $log_path"
}

run_canvas_core_frontend_checks() {
  local run_dir="$evidence_dir/run-$run_stamp"
  local isolated_dist="/private/tmp/ecom-v011-frontend-preview/dist"
  mkdir -p "$run_dir"
  echo "Canvas operations and node execution frontend verification: $run_stamp"
  echo 'Scope: current frontend source + isolated build + independent HTTP restart; real media/LLM and browser acceptance remain separate'
  "$app_python" scripts/check-v011-files.py snapshot-live-build "$run_dir/live-build-before.json"
  echo '1/5 Frontend lint, strict types and isolated production build'
  node --version
  npm --version
  npm --prefix frontend run lint
  npm --prefix frontend run typecheck
  npm --prefix frontend run build -- --outDir "$isolated_dist" --emptyOutDir
  "$app_python" scripts/check-v011-files.py snapshot-isolated-build "$run_dir/isolated-build-sha256.json"
  echo '2/5 Full frontend test suite'
  npm --prefix frontend test -- --run
  echo '3/5 Credential exclusion, including the isolated frontend build'
  "$app_python" scripts/check-v011-files.py secrets-frontend
  echo '4/5 Independent HTTP restart: queued node execution, deletion history, undo and idempotent replay'
  CANVAS_CORE_EVIDENCE_DIR="$run_dir" "$app_python" scripts/check-canvas-core-restart.py
  echo '5/5 Required technical files, whitespace and unchanged existing frontend build'
  "$app_python" scripts/check-v011-files.py files-canvas-core-frontend
  "$app_python" scripts/check-v011-files.py check-live-build "$run_dir/live-build-before.json"
  echo 'PASS: frontend automated checks only; restart worker disabled; no real provider, browser or complete Agent workflow acceptance claim'
  echo "Evidence: $log_path"
}

run_director_workflow_checks() {
  local run_dir="$evidence_dir/run-$run_stamp"
  mkdir -p "$run_dir"
  echo "Director conversation and tools backend verification: $run_stamp"
  echo 'Scope: isolated database, fixed LLM/media runners, real local FFmpeg; no business data or provider calls'
  "$app_python" scripts/check-v011-files.py snapshot-live-build "$run_dir/live-build-before.json"
  echo '1/5 Full backend suite and retained legacy director regressions'
  "$app_python" -m unittest discover -s backend/tests -v
  "$app_python" scripts/check-v011-legacy.py
  echo '2/5 Credential pattern exclusion'
  "$app_python" scripts/check-v011-files.py secrets
  echo '3/5 Actual HTTP workflow, real local concatenation and process restart'
  "$app_python" scripts/check-director-workflow-http.py --evidence-dir "$run_dir/http-clips"
  "$app_python" scripts/check-director-workflow-http.py --film --evidence-dir "$run_dir/http-film"
  echo '4/5 Frontend N/A: new conversational API not connected to the UI in this backend batch'
  echo '5/5 Required files, whitespace and unchanged existing frontend build'
  "$app_python" scripts/check-v011-files.py files-director-workflow
  "$app_python" scripts/check-v011-files.py check-live-build "$run_dir/live-build-before.json"
  echo 'PASS: backend code and isolated checks only; real-model and frontend conversation acceptance remain pending'
  echo "Evidence: $log_path"
}

run_director_workflow_frontend_checks() {
  local run_dir="$evidence_dir/run-$run_stamp"
  local isolated_dist="${V011_WORKFLOW_DIST:-/private/tmp/ecom-v011-frontend-preview/workflow-dist}"
  mkdir -p "$run_dir"
  echo "Director conversation workflow frontend verification: $run_stamp"
  echo 'Scope: current frontend source, isolated workflow build and fixed-provider HTTP workflows; no business data, real model call or browser acceptance'
  "$app_python" scripts/check-v011-files.py snapshot-live-build "$run_dir/live-build-before.json"
  "$app_python" scripts/check-v011-files.py snapshot-workflow-source "$run_dir/frontend-input-sha256.json"
  echo '1/5 Frontend lint, strict types and isolated workflow production build'
  node --version
  npm --version
  npm --prefix frontend run lint
  npm --prefix frontend run typecheck
  npm --prefix frontend run build -- --outDir "$isolated_dist" --emptyOutDir
  "$app_python" scripts/check-v011-files.py snapshot-workflow-build "$run_dir/workflow-build-sha256.json"
  echo '2/5 Full frontend test suite'
  npm --prefix frontend test -- --run
  echo '3/5 Credential pattern exclusion, including the isolated workflow build'
  "$app_python" scripts/check-v011-files.py secrets-workflow-frontend
  echo '4/5 Independent HTTP workflow and process restart with fixed LLM/media and real local FFmpeg'
  "$app_python" scripts/check-director-workflow-http.py --evidence-dir "$run_dir/http-clips"
  "$app_python" scripts/check-director-workflow-http.py --film --evidence-dir "$run_dir/http-film"
  echo '5/5 Required technical files, whitespace and unchanged existing frontend build'
  "$app_python" scripts/check-v011-files.py files-director-workflow-frontend
  if [[ "$mode" == "frontend-closing" ]]; then
    "$app_python" scripts/check-v011-files.py files-frontend-closing
  fi
  "$app_python" scripts/check-v011-files.py check-live-build "$run_dir/live-build-before.json"
  "$app_python" scripts/check-v011-files.py check-workflow-source "$run_dir/frontend-input-sha256.json"
  "$app_python" scripts/check-v011-files.py check-workflow-build "$run_dir/workflow-build-sha256.json"
  echo 'PASS: frontend automated checks and fixed-provider HTTP recovery only; browser, real-model quality and final product acceptance remain separate'
  echo "Evidence: $log_path"
}

run_budget_reservation_checks() {
  local run_dir="$evidence_dir/run-$run_stamp"
  mkdir -p "$run_dir"
  echo "Historical reservation regression: $run_stamp"
  echo 'Scope: isolated databases, blocked model transports, fixed media and real local FFmpeg; zero paid calls'
  "$app_python" scripts/check-v011-files.py snapshot-live-build "$run_dir/live-build-before.json"
  echo '1/5 Full backend and retained legacy director tests'
  "$app_python" -m unittest discover -s backend/tests -v
  "$app_python" scripts/check-v011-legacy.py
  echo '2/5 Credential pattern exclusions'
  "$app_python" scripts/check-v011-files.py secrets
  echo '3/5 HTTP image/video/film workflow and restart WITH unknown historical video reservation'
  "$app_python" scripts/check-director-workflow-http.py --legacy-budget-reservation --evidence-dir "$run_dir/http-clips"
  "$app_python" scripts/check-director-workflow-http.py --legacy-budget-reservation --film --evidence-dir "$run_dir/http-film"
  echo '4/5 Frontend unchanged: verify released build fingerprints'
  "$app_python" scripts/check-v011-files.py check-live-build "$run_dir/live-build-before.json"
  echo '5/5 Technical files, Python syntax and whitespace'
  "$app_python" scripts/check-v011-files.py files-director-workflow
  "$app_python" -m py_compile backend/app/config.py backend/app/node_execution.py backend/app/errors.py scripts/check-director-workflow-http.py
  git diff --check
  echo 'PASS: historical reservation fix and free isolated HTTP recovery; paid media and user acceptance remain separate'
  echo "Evidence: $log_path"
}

if [[ "$mode" == "budget-reservation-fix" ]]; then
  run_budget_reservation_checks 2>&1 | tee "$log_path"
  cp "$log_path" "$evidence_dir/verify-输出.txt"
elif [[ "$mode" == "frontend-closing" ]]; then
  run_director_workflow_frontend_checks 2>&1 | tee "$log_path"
elif [[ "$mode" == "director-workflow-frontend" ]]; then
  run_director_workflow_frontend_checks 2>&1 | tee "$log_path"
elif [[ "$mode" == "director-workflow-backend" ]]; then
  run_director_workflow_checks 2>&1 | tee "$log_path"
elif [[ "$mode" == "canvas-core-frontend" ]]; then
  run_canvas_core_frontend_checks 2>&1 | tee "$log_path"
  cp "$log_path" "$evidence_dir/verify-输出.txt"
elif [[ "$mode" == "canvas-core-backend" ]]; then
  run_canvas_core_checks 2>&1 | tee "$log_path"
elif [[ "$mode" == "v0.11-frontend" ]]; then
  run_frontend_checks 2>&1 | tee "$log_path"
  cp "$log_path" "$evidence_dir/verify-输出.txt"
else
  run_checks 2>&1 | tee "$log_path"
fi
