#!/usr/bin/env python3
"""S5–S6 免费验收：成片纯拼接 / 缺镜阻断 / 部分合成 / 结构模板 / 导出。

覆盖：
1. 缺镜 → FILM_MISSING_SHOTS（不产出"完整成片"）
2. confirm_partial=true → 允许部分合成，status=partial，列出缺镜
3. 全部成功 → status=ready；计划时长与实测时长分字段
4. 成片版本只增不覆盖，历史保留
5. 结构模板：保存/查看/套用；不含商品事实；套用后镜头骨架可编辑
6. 导出 zip 含 manifest.json
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import assembly, config, db, projects, scope, shots, storage  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "S5-S6-20261026"
BRIEF = {"subject": "单瓶精华液", "quantity": 1}
V10 = "doubao-seedance-1-0-pro-fast-251015"
FLASH = "doubao-seedream-5-0-flash-260915"
SPECS = {"model_id": V10, "ratio": "9:16", "resolution": "720p"}
ASSET = {"sb": None, "clip": None}


def case(name: str, fn) -> None:
    try:
        d = fn() or {}
        RESULTS.append({"case": name, "ok": True, "detail": d})
        print(f"  OK  {name}" + (f"  {d}" if d else ""))
    except AssertionError as e:
        RESULTS.append({"case": name, "ok": False, "error": str(e)})
        print(f" FAIL {name} → {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"case": name, "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-400:]})
        print(f" ERR  {name} → {type(e).__name__}: {e}")


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 205, 215)).save(buf, format="PNG")
    return buf.getvalue()


def setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="s56-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()
    ASSET["sb"] = storage.save_bytes(_png(), "image", "upload")["id"]
    ASSET["clip"] = storage.save_bytes(_png(), "video", "cloud_generated")["id"]
    return tmp


def expect(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http
        return {"code": e.code, "http": e.http}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def project_with_takes(n: int = 2, succeed: int | None = None) -> str:
    pid = projects.create_project("S5 项目", BRIEF)["project_id"]
    s = projects.add_script(pid, {"summary": "三段式"}, source="manual")
    projects.confirm_script(s["script_id"])
    out = shots.add_shots(pid, s["script_id"],
                          [{"description": f"镜{i}", "duration_s": 5} for i in range(1, n + 1)])
    for sid in out["shot_ids"]:
        g = shots.generate_storyboard(sid, FLASH, confirm=True)
        shots.attach_storyboard_asset(g["storyboard_id"], ASSET["sb"])
        shots.review_storyboard(g["storyboard_id"], approve=True)
    sc = scope.create_scope(pid, "full", out["shot_ids"], SPECS)
    scope.confirm_scope(sc["scope_id"])
    sub = scope.submit_scope(sc["scope_id"])
    ok = n if succeed is None else succeed
    for i, t in enumerate(sub["tasks"]):
        if i < ok:
            db.update("shot_takes", t["take_id"],
                      {"status": "succeeded", "asset_id": ASSET["clip"],
                       "duration_ms": 5000})
        else:
            db.update("shot_takes", t["take_id"], {"status": "failed"})
    return pid


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）\n")

    print("=== S5：成片拼接 ===")

    def missing_blocks():
        pid = project_with_takes(2, succeed=1)
        d = expect("FILM_MISSING_SHOTS", lambda: assembly.compose(pid), 409)
        assert db.query("SELECT COUNT(*) c FROM films WHERE project_id=?", (pid,))[0]["c"] == 0, \
            "缺镜时不应产出成片记录"
        return d
    case("缺镜 → FILM_MISSING_SHOTS（不产出成片）", missing_blocks)

    def partial_needs_consent():
        pid = project_with_takes(2, succeed=1)
        f = assembly.compose(pid, confirm_partial=True)
        assert f["status"] == "partial" and f["partial"] is True
        assert len(f["missing_shots"]) == 1
        # 计划时长 vs 实测时长分字段
        assert f["planned_duration_s"] == 10, f["planned_duration_s"]
        assert f["actual_duration_ms"] == 5000, f["actual_duration_ms"]
        return {"status": f["status"], "missing": len(f["missing_shots"]),
                "planned_s": f["planned_duration_s"], "actual_ms": f["actual_duration_ms"]}
    case("显式 confirm_partial → partial 成片（列出缺镜 + 计划/实测分开）",
         partial_needs_consent)

    def full_ready_and_versions():
        pid = project_with_takes(2, succeed=2)
        f1 = assembly.compose(pid)
        assert f1["status"] == "ready" and f1["partial"] is False
        assert f1["actual_duration_ms"] == 10000, f1["actual_duration_ms"]
        f2 = assembly.compose(pid)
        assert f2["version"] == f1["version"] + 1, (f1["version"], f2["version"])
        old = assembly.get_film(f1["film_id"])
        assert old["status"] == "ready", "历史成片被覆盖"
        note = f1["note"]
        assert "不裁剪" in note
        return {"v1": f1["version"], "v2": f2["version"], "note": note}
    case("全部成功 → ready；成片版本只增不覆盖；纯拼接", full_ready_and_versions)

    def films_listed():
        pid = project_with_takes(1, succeed=1)
        assembly.compose(pid)
        rows = assembly.db.query("SELECT id FROM films WHERE project_id=?", (pid,))
        assert len(rows) == 1
        return {"films": len(rows)}
    case("成片可列出（历史保留）", films_listed)

    print("\n=== S6：结构模板 ===")

    def save_and_get():
        tpl = assembly.save_template("3 镜快节奏", [
            {"seq": 1, "role": "开场特写", "duration_s": 4, "motion": "推近", "rhythm": "快"},
            {"seq": 2, "role": "使用场景", "duration_s": 6, "motion": "环绕", "rhythm": "中"},
            {"seq": 3, "role": "卖点收尾", "duration_s": 5, "motion": "静止", "rhythm": "慢"}])
        got = assembly.get_template(tpl["template_id"])
        assert len(got["snapshot"]) == 3
        assert "商品" not in json.dumps(got["snapshot"], ensure_ascii=False), "模板不应含商品事实"
        return {"template_id": tpl["template_id"][:12] + "…", "version": got["version"]}
    case("保存/查看结构模板（只含结构，不含商品事实）", save_and_get)

    def template_duration_strict():
        expect("MODEL_CAPABILITY_MISMATCH",
               lambda: assembly.save_template("坏时长", [{"seq": 1, "duration_s": 5.5}]), 400)
        expect("MODEL_CAPABILITY_MISMATCH",
               lambda: assembly.save_template("越界", [{"seq": 1, "duration_s": 99}]), 400)
        return {"5.5": "rejected", "99": "rejected"}
    case("模板时长同样严格（5.5 / 99 拒绝）", template_duration_strict)

    def save_from_project_and_apply():
        pid = project_with_takes(2, succeed=2)
        tpl = assembly.save_from_project(pid, "项目结构")
        pid2 = projects.create_project("套用项目", BRIEF)["project_id"]
        s = projects.add_script(pid2, {"summary": "载体脚本"}, source="manual")
        projects.confirm_script(s["script_id"])
        out = assembly.apply_template(pid2, tpl["template_id"])
        assert out["created_shots"] and len(out["created_shots"]) == 2
        assert out["requires"] == ["brief", "assets"]
        # 套用后项目 brief 仍需重填（不继承）
        detail = projects.project_detail(pid2)
        assert detail["brief"]["subject"] == "单瓶精华液"
        # 项目记录模板版本
        assert detail["structure_template_id"] == tpl["template_id"]
        assert detail["structure_template_version"] == 1
        return {"created": len(out["created_shots"]), "version": out["template_version"]}
    case("从项目保存结构并套用（重绑素材/重校验模型）", save_from_project_and_apply)

    print("\n=== S6：项目导出 ===")

    def export_zip():
        pid = project_with_takes(1, succeed=1)
        assembly.compose(pid)
        out = assembly.export_project(pid)
        path = Path(out["path"])
        assert path.exists() and out["bytes"] > 0
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
        assert "manifest.json" in names
        assert manifest["project_id"] == pid and manifest["shots"]
        return {"files": len(names), "bytes": out["bytes"],
                "has_manifest": True}
    case("导出 zip 包含 manifest 与产物清单", export_zip)

    def export_no_regeneration():
        pid = project_with_takes(1, succeed=1)
        n = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assembly.export_project(pid)
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == n, "导出触发了新任务"
        return {"tasks": n, "unchanged": True}
    case("导出不重新生成（任务数不变）", export_no_regeneration)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "S5 成片拼接 + S6 结构模板 / 导出",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "worker": "未启动", "real_model_calls": 0,
                      "note": "使用自制 32×32 素材占位；未执行真实合成为 mp4"},
        "paid_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "ffmpeg 真实拼接未在本轮验证（无真实 mp4 片段）；本轮验证编排与状态语义。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实模型 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
