#!/usr/bin/env bash
# Existing-project adapter for the starter kit's five checks; stage modes stay separate.
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:-}"
if [[ "$mode" == "home-ui" ]]; then
  home_evidence="docs/evidence/首页视觉更新-20261102"
  home_dist="/private/tmp/ecom-home-ui-20261102/dist"
  mkdir -p "$home_evidence"
  home_log="$home_evidence/verify-$(date +%Y%m%dT%H%M%S)-$$.txt"
  (
    echo 'Homepage visual update: frontend-only verification'
    npm --prefix frontend run lint
    npm --prefix frontend run test
    npm --prefix frontend run build -- --outDir "$home_dist" --emptyOutDir
    V011_WORKFLOW_DIST="$home_dist" "${APP_PYTHON:-python3}" scripts/check-v011-files.py secrets-workflow-frontend
    git diff --check -- frontend/src/ProjectHome.tsx frontend/src/ProjectHome.css verify.sh
    for home_file in frontend/src/assets/home/director-scene.png frontend/src/assets/home/quick-scene.png docs/阶段文档/首页视觉更新-20261102.md "$home_evidence/是否表.md" "$home_evidence/照着做清单.md"; do
      test -s "$home_file" || { echo "Missing: $home_file"; exit 1; }
    done
    echo 'PASS: frontend checks, isolated build, credential scan and required files'
    echo 'N/A: backend restart and paid model generation; no backend/data contract changes'
  ) 2>&1 | tee "$home_log"
  exit 0
fi
acceptance_backend=false
selected_models_backend=false
multi_acceptance_backend=false
if [[ "$mode" == "aihubmix-multi-acceptance-backend" ]]; then
  multi_acceptance_backend=true
  mode="aihubmix-expansion-backend"
fi
if [[ "$mode" == "aihubmix-selected-backend" ]]; then
  selected_models_backend=true
  mode="aihubmix-expansion-backend"
fi
if [[ "$mode" == "aihubmix-acceptance-backend" ]]; then
  acceptance_backend=true
  mode="aihubmix-expansion-backend"
fi
if [[ "$mode" != "aihubmix-expansion-backend" && "$mode" != "aihubmix-selection-frontend" && "$mode" != "aihubmix-selection-backend" && "$mode" != "shadow-continuity-backend" && "$mode" != "video-framing-backend" && "$mode" != "media-policy-backend" && "$mode" != "aihubmix-backend" && "$mode" != "v0.11-backend" && "$mode" != "v0.11-frontend" && "$mode" != "canvas-core-backend" && "$mode" != "canvas-core-frontend" && "$mode" != "director-workflow-backend" && "$mode" != "director-workflow-frontend" && "$mode" != "frontend-closing" && "$mode" != "budget-reservation-fix" && "$mode" != "director-context" && "$mode" != "director-reference" && "$mode" != "web-closing" && "$mode" != "director-clarification" && "$mode" != "director-status" && "$mode" != "model-specs" && "$mode" != "model-specs-frontend" && "$mode" != "model-specs-effective" ]]; then
  echo 'Usage: APP_PYTHON=/absolute/python bash verify.sh aihubmix-selected-backend|aihubmix-acceptance-backend|aihubmix-expansion-backend|aihubmix-selection-frontend|aihubmix-selection-backend|shadow-continuity-backend|video-framing-backend|media-policy-backend|aihubmix-backend|v0.11-backend|v0.11-frontend|canvas-core-backend|canvas-core-frontend|director-workflow-backend|director-workflow-frontend|frontend-closing|budget-reservation-fix|director-context|director-reference|web-closing|director-clarification|director-status|model-specs|model-specs-frontend|model-specs-effective'
  exit 2
fi
app_python="${APP_PYTHON:-python3}"
if [[ "$mode" == "aihubmix-expansion-backend" ]]; then
  if [[ "$multi_acceptance_backend" == true ]]; then
    evidence_dir="docs/evidence/v0.11-FLUX-H3-Wan真实验收-20261030"
  elif [[ "$selected_models_backend" == true ]]; then
    evidence_dir="docs/evidence/v0.11-八款模型扩充-后端-20261030"
  elif [[ "$acceptance_backend" == true ]]; then
    evidence_dir="docs/evidence/v0.11-Qwen-Mini真实验收-20261030"
  else
    evidence_dir="docs/evidence/v0.11-AiHubMix扩充-后端-20261030"
  fi
elif [[ "$mode" == "aihubmix-selection-frontend" ]]; then
  evidence_dir="docs/evidence/v0.11-模型选择-前端-20261030"
  export V011_WORKFLOW_DIST="/private/tmp/ecom-v011-frontend-preview/aihubmix-selection-dist"
elif [[ "$mode" == "aihubmix-selection-backend" ]]; then
  evidence_dir="docs/evidence/v0.11-模型启用与覆盖-后端-20261030"
elif [[ "$mode" == "shadow-continuity-backend" ]]; then
  evidence_dir="docs/evidence/v0.11-阴影连续性-后端-20261030"
elif [[ "$mode" == "video-framing-backend" ]]; then
  evidence_dir="docs/evidence/v0.11-视频构图与补边-后端-20261030"
elif [[ "$mode" == "media-policy-backend" ]]; then
  evidence_dir="docs/evidence/v0.11-无水印与完整构图-后端-20261030"
elif [[ "$mode" == "aihubmix-backend" ]]; then
  evidence_dir="docs/evidence/v0.11-AiHubMix接入-后端-20261030"
elif [[ "$mode" == "model-specs-frontend" ]]; then
  evidence_dir="docs/evidence/v0.11-模型规格组合-前端-20261029"
  export V011_WORKFLOW_DIST="/private/tmp/ecom-v011-frontend-preview/model-specs-dist"
elif [[ "$mode" == "model-specs-effective" ]]; then
  evidence_dir="docs/evidence/v0.11-规格参数衔接-后端-20261029"
elif [[ "$mode" == "model-specs" ]]; then
  evidence_dir="docs/evidence/v0.11-模型规格组合-后端-20261029"
elif [[ "$mode" == "director-status" ]]; then
  evidence_dir="docs/evidence/v0.11-Agent状态描述-后端-20261029"
elif [[ "$mode" == "director-clarification" ]]; then
  evidence_dir="docs/evidence/v0.11-Agent澄清修复-后端-20261029"
elif [[ "$mode" == "web-closing" ]]; then
  evidence_dir="docs/evidence/v0.11-Web收尾-20261029"
  export V011_WORKFLOW_DIST="/private/tmp/ecom-v011-frontend-preview/web-closing-dist"
elif [[ "$mode" == "director-reference" ]]; then
  evidence_dir="docs/evidence/v0.11-分镜共用参考图-后端-20261029"
elif [[ "$mode" == "director-context" ]]; then
  evidence_dir="docs/evidence/v0.11-长对话上下文-后端-20261029"
elif [[ "$mode" == "budget-reservation-fix" ]]; then
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
  if [[ "$mode" == "aihubmix-selection-frontend" ]]; then
    "$app_python" scripts/check-director-workflow-http.py --aihubmix --shared-reference --legacy-budget-reservation --evidence-dir "$run_dir/hub-clips"
    "$app_python" scripts/check-director-workflow-http.py --aihubmix --shared-reference --legacy-budget-reservation --film --evidence-dir "$run_dir/hub-film"
  fi
  echo '5/5 Required technical files, whitespace and unchanged existing frontend build'
  "$app_python" scripts/check-v011-files.py files-director-workflow-frontend
  if [[ "$mode" == "frontend-closing" ]]; then
    "$app_python" scripts/check-v011-files.py files-frontend-closing
  fi
  if [[ "$mode" == "web-closing" ]]; then
    "$app_python" -c "from pathlib import Path; paths=['frontend/src/features.ts','frontend/src/media-export.ts','frontend/src/media-export.test.ts','frontend/src/features.test.tsx','docs/阶段文档/Web收尾-20261029.md','docs/evidence/v0.11-Web收尾-20261029/是否表.md','docs/evidence/v0.11-Web收尾-20261029/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in paths); print('PASS: 7 Web closing source/evidence files; layout PNG editor explicitly deferred')"
  fi
  if [[ "$mode" == "model-specs-frontend" ]]; then
    "$app_python" -c "from pathlib import Path; paths=['frontend/src/model-specs.ts','frontend/src/model-specs.test.tsx','docs/阶段文档/模型规格组合-前端-20261029.md','docs/evidence/v0.11-模型规格组合-前端-20261029/是否表.md','docs/evidence/v0.11-模型规格组合-前端-20261029/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in paths); print('PASS: model combinations frontend files')"
  fi
  if [[ "$mode" == "aihubmix-selection-frontend" ]]; then
    "$app_python" -c "from pathlib import Path; paths=['frontend/src/model-catalog.ts','frontend/src/task-actions.ts','frontend/src/task-actions.test.ts','frontend/src/workbench-models.ts','frontend/src/workbench-models.test.ts','docs/阶段文档/模型选择-前端-20261030.md','docs/evidence/v0.11-模型选择-前端-20261030/是否表.md','docs/evidence/v0.11-模型选择-前端-20261030/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in paths); print('PASS: model selection frontend source and evidence files')"
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
  echo "Backend context/reservation regression ($mode): $run_stamp"
  echo 'Scope: isolated databases, blocked model transports, fixed media and real local FFmpeg; zero paid calls'
  "$app_python" scripts/check-v011-files.py snapshot-live-build "$run_dir/live-build-before.json"
  echo '1/5 Full backend and retained legacy director tests'
  "$app_python" -m unittest discover -s backend/tests -v
  "$app_python" scripts/check-v011-legacy.py
  echo '2/5 Credential pattern exclusions'
  "$app_python" scripts/check-v011-files.py secrets
  echo '3/5 HTTP image/video/film workflow and restart WITH unknown historical video reservation'
  if [[ "$mode" == "aihubmix-expansion-backend" || "$mode" == "aihubmix-selection-backend" || "$mode" == "shadow-continuity-backend" || "$mode" == "video-framing-backend" || "$mode" == "media-policy-backend" ]]; then
    "$app_python" scripts/check-director-workflow-http.py --aihubmix --shared-reference --legacy-budget-reservation --evidence-dir "$run_dir/hub-clips"
    "$app_python" scripts/check-director-workflow-http.py --aihubmix --shared-reference --legacy-budget-reservation --film --evidence-dir "$run_dir/hub-film"
    "$app_python" scripts/check-director-workflow-http.py --shared-reference --legacy-budget-reservation --evidence-dir "$run_dir/ark-clips"
    "$app_python" scripts/check-director-workflow-http.py --shared-reference --legacy-budget-reservation --film --evidence-dir "$run_dir/ark-film"
  elif [[ "$mode" == "aihubmix-backend" ]]; then
    "$app_python" scripts/check-director-workflow-http.py --aihubmix --shared-reference --legacy-budget-reservation --evidence-dir "$run_dir/http-clips"
    "$app_python" scripts/check-director-workflow-http.py --aihubmix --shared-reference --legacy-budget-reservation --film --evidence-dir "$run_dir/http-film"
  elif [[ "$mode" == "model-specs-effective" ]]; then
    "$app_python" scripts/check-director-workflow-http.py --spec-combinations --shared-reference --legacy-budget-reservation --evidence-dir "$run_dir/http-clips"
    "$app_python" scripts/check-director-workflow-http.py --spec-combinations --shared-reference --legacy-budget-reservation --film --evidence-dir "$run_dir/http-film"
  elif [[ "$mode" == "director-reference" ]]; then
    "$app_python" scripts/check-director-workflow-http.py --shared-reference --legacy-budget-reservation --evidence-dir "$run_dir/http-clips"
    "$app_python" scripts/check-director-workflow-http.py --shared-reference --legacy-budget-reservation --film --evidence-dir "$run_dir/http-film"
  else
    "$app_python" scripts/check-director-workflow-http.py --legacy-budget-reservation --evidence-dir "$run_dir/http-clips"
    "$app_python" scripts/check-director-workflow-http.py --legacy-budget-reservation --film --evidence-dir "$run_dir/http-film"
  fi
  echo '4/5 Frontend unchanged: verify released build fingerprints'
  "$app_python" scripts/check-v011-files.py check-live-build "$run_dir/live-build-before.json"
  if [[ "$mode" == "aihubmix-expansion-backend" || "$mode" == "aihubmix-selection-backend" || "$mode" == "shadow-continuity-backend" || "$mode" == "video-framing-backend" || "$mode" == "media-policy-backend" || "$mode" == "aihubmix-backend" ]]; then
    "$app_python" -m py_compile backend/app/media_policy.py backend/tests/test_media_policy.py
    "$app_python" -m py_compile backend/app/aihubmix_models.py backend/app/providers_aihubmix.py backend/app/worker.py backend/app/cloud_tasks.py backend/tests/test_aihubmix.py
  fi
  if [[ "$mode" == "video-framing-backend" ]]; then
    "$app_python" scripts/check-video-framing-offline.py --evidence-dir "$evidence_dir"
    "$app_python" -m py_compile backend/tests/test_ark_output_policy_recovery.py scripts/check-video-framing-offline.py
    "$app_python" -c "from pathlib import Path; names=['docs/阶段文档/视频构图与补边-后端-20261030.md','docs/evidence/v0.11-视频构图与补边-后端-20261030/是否表.md','docs/evidence/v0.11-视频构图与补边-后端-20261030/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: video framing batch documents present')"
  fi
  if [[ "$mode" == "shadow-continuity-backend" ]]; then
    "$app_python" scripts/check-video-framing-offline.py --evidence-dir "$evidence_dir"
    "$app_python" scripts/check-shadow-continuity-offline.py --evidence-dir "$evidence_dir"
    "$app_python" -m py_compile backend/tests/test_shadow_padding.py scripts/check-shadow-continuity-offline.py
    "$app_python" -c "from pathlib import Path; names=['docs/阶段文档/阴影连续性-后端-20261030.md','docs/evidence/v0.11-阴影连续性-后端-20261030/是否表.md','docs/evidence/v0.11-阴影连续性-后端-20261030/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: shadow continuity evidence documents present')"
  fi
  if [[ "$mode" == "aihubmix-selection-backend" ]]; then
    "$app_python" -m py_compile backend/tests/test_aihubmix_activation.py backend/tests/test_aihubmix_activation_paths.py backend/tests/test_aihubmix_reservation_recovery.py
    "$app_python" -c "from pathlib import Path; names=['docs/阶段文档/模型启用与覆盖-后端-20261030.md','docs/evidence/v0.11-模型启用与覆盖-后端-20261030/是否表.md','docs/evidence/v0.11-模型启用与覆盖-后端-20261030/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: model selection backend documents present')"
  fi
  if [[ "$mode" == "aihubmix-expansion-backend" ]]; then
    "$app_python" -m py_compile backend/tests/test_aihubmix_expansion.py
    "$app_python" -c "from pathlib import Path; names=['docs/阶段文档/AiHubMix扩充-后端-20261030.md','docs/evidence/v0.11-AiHubMix扩充-后端-20261030/是否表.md','docs/evidence/v0.11-AiHubMix扩充-后端-20261030/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: expansion documents present')"
  fi
  if [[ "$acceptance_backend" == true ]]; then
    "$app_python" -m py_compile backend/tests/test_aihubmix_sample_metadata.py backend/tests/test_aihubmix_accepted_models.py
    "$app_python" -c "from pathlib import Path; names=['docs/阶段文档/Qwen-Mini真实验收-20261030.md','docs/evidence/v0.11-Qwen-Mini真实验收-20261030/是否表.md','docs/evidence/v0.11-Qwen-Mini真实验收-20261030/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: real acceptance documents present')"
  fi
  if [[ "$selected_models_backend" == true ]]; then
    "$app_python" -m py_compile backend/app/aihubmix_extra_images.py backend/app/aihubmix_extra_videos.py backend/tests/test_aihubmix_extra_images.py backend/tests/test_aihubmix_extra_videos.py backend/tests/test_aihubmix_selected_integration.py
    "$app_python" -c "from pathlib import Path; names=['docs/阶段文档/八款模型扩充-后端-20261030.md','docs/evidence/v0.11-八款模型扩充-后端-20261030/是否表.md','docs/evidence/v0.11-八款模型扩充-后端-20261030/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: selected-model integration documents present')"
  fi
  if [[ "$multi_acceptance_backend" == true ]]; then
    "$app_python" -m py_compile backend/app/aihubmix_audio.py backend/tests/test_aihubmix_multi_acceptance.py backend/tests/test_aihubmix_silent_output.py backend/tests/test_aihubmix_selected_defaults.py
    "$app_python" -c "from pathlib import Path; names=['docs/阶段文档/FLUX-H3-Wan真实验收-20261030.md','$evidence_dir/是否表.md','$evidence_dir/照着做清单.md','$evidence_dir/h3-audio-processing-proof.json']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: FLUX H3 Wan real acceptance and silent-delivery evidence present')"
  fi
  echo '5/5 Technical files, Python syntax and whitespace'
  "$app_python" scripts/check-v011-files.py files-director-workflow
  "$app_python" -m py_compile backend/app/config.py backend/app/node_execution.py backend/app/errors.py scripts/check-director-workflow-http.py
  if [[ "$mode" == "director-context" || "$mode" == "director-reference" || "$mode" == "director-clarification" || "$mode" == "director-status" || "$mode" == "model-specs" || "$mode" == "model-specs-effective" ]]; then
    "$app_python" -m py_compile backend/app/director_context.py backend/app/director_workflow.py backend/app/director_chat.py scripts/check-director-recorded-context.py
    "$app_python" -m py_compile backend/app/director_creation.py
    "$app_python" -c "from pathlib import Path; paths=['backend/app/director_context.py','backend/tests/test_director_context.py','docs/阶段文档/长对话上下文-后端-20261029.md','docs/evidence/v0.11-长对话上下文-后端-20261029/是否表.md','docs/evidence/v0.11-长对话上下文-后端-20261029/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in paths); print('PASS: 5 context batch technical/evidence files')"
    if [[ "$mode" == "director-reference" ]]; then
      "$app_python" -c "from pathlib import Path; paths=['docs/阶段文档/分镜共用参考图-后端-20261029.md','docs/evidence/v0.11-分镜共用参考图-后端-20261029/是否表.md','docs/evidence/v0.11-分镜共用参考图-后端-20261029/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in paths); print('PASS: 3 shared-reference batch evidence files')"
    fi
    if [[ -f data/acceptance/run-20261028-real-v011/acceptance.db ]]; then
      "$app_python" scripts/check-director-recorded-context.py
    else
      echo 'SKIP: optional recorded-data replay; local acceptance database is not present (not shipped in Git)'
    fi
  fi
  if [[ "$mode" == "model-specs" || "$mode" == "model-specs-effective" ]]; then
    "$app_python" -m py_compile backend/app/model_specs.py backend/tests/test_model_specs.py
    "$app_python" -c "from pathlib import Path; names=['backend/app/model_specs.py','backend/tests/test_model_specs.py','docs/阶段文档/模型规格组合-后端-20261029.md','docs/evidence/v0.11-模型规格组合-后端-20261029/是否表.md','docs/evidence/v0.11-模型规格组合-后端-20261029/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: 5 model specification batch files')"
  fi
  if [[ "$mode" == "model-specs-effective" ]]; then
    "$app_python" -m py_compile backend/app/canvas.py backend/app/main.py backend/app/shots.py backend/app/scope.py backend/app/routers/creative.py
    "$app_python" -c "from pathlib import Path; names=['docs/阶段文档/规格参数衔接-后端-20261029.md','docs/evidence/v0.11-规格参数衔接-后端-20261029/是否表.md','docs/evidence/v0.11-规格参数衔接-后端-20261029/照着做清单.md']; assert all(Path(p).is_file() and Path(p).stat().st_size for p in names); print('PASS: effective specification batch evidence files')"
  fi
  git diff --check
  echo 'PASS: backend regressions and free isolated HTTP recovery; paid media and user acceptance remain separate'
  echo "Evidence: $log_path"
}

if [[ "$mode" == "aihubmix-expansion-backend" || "$mode" == "aihubmix-selection-backend" || "$mode" == "shadow-continuity-backend" || "$mode" == "video-framing-backend" || "$mode" == "media-policy-backend" || "$mode" == "aihubmix-backend" || "$mode" == "budget-reservation-fix" || "$mode" == "director-context" || "$mode" == "director-reference" || "$mode" == "director-clarification" || "$mode" == "director-status" || "$mode" == "model-specs" || "$mode" == "model-specs-effective" ]]; then
  run_budget_reservation_checks 2>&1 | tee "$log_path"
  cp "$log_path" "$evidence_dir/verify-输出.txt"
elif [[ "$mode" == "aihubmix-selection-frontend" || "$mode" == "frontend-closing" || "$mode" == "web-closing" || "$mode" == "model-specs-frontend" ]]; then
  run_director_workflow_frontend_checks 2>&1 | tee "$log_path"
  if [[ "$mode" == "aihubmix-selection-frontend" || "$mode" == "web-closing" || "$mode" == "model-specs-frontend" ]]; then cp "$log_path" "$evidence_dir/verify-输出.txt"; fi
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
