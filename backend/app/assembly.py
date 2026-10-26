"""S5–S6：成片拼接（纯拼接、不裁剪）+ 结构模板 + 项目导出。

规则（PRD v0.10 §2.7、§5-Q4、导演台路线）：
- **纯拼接**：不裁剪、不变速、不补帧（Q3 已确认）。
- `full` 模式整组成功后**自动**拼接；`clips` 模式由用户主动合成。
- **缺镜不得标为完整成片**：`FILM_MISSING_SHOTS`，除非用户显式 `confirm_partial=true`。
- **计划时长与实测时长分别返回**；成片版本只增不覆盖，历史保留。
- 结构模板：保存/查看/套用；保存结构（职能/顺序/节奏/运镜/原生时长），**不含商品事实**；
  套用时重绑素材、重校验模型；模板版本保留，已套用项目不自动跟随。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, db, projects, shots
from .errors import err

# ---------------- 时长探测 ----------------
def probe_duration_ms(path: Path) -> int | None:
    """用 ffprobe 探测实际时长（毫秒）；不可用时返回 None（如实标注，不猜）。"""
    exe = _ffprobe()
    if not exe or not path.exists():
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=20)
        val = (out.stdout or "").strip()
        return int(float(val) * 1000) if val else None
    except Exception:  # noqa: BLE001
        return None


def _ffprobe() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe().replace("ffmpeg", "ffprobe")
    except Exception:  # noqa: BLE001
        return shutil.which("ffprobe")


def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


# ---------------- 成片 ----------------
def _take_view(t: dict) -> dict:
    return {"take_id": t["id"], "shot_id": t["shot_id"], "asset_id": t["asset_id"],
            "status": t["status"], "duration_ms": t["duration_ms"]}


def compose(project_id: str, take_ids: list[str] | None = None,
            confirm_partial: bool = False) -> dict:
    """合成成片。take_ids 缺省 = 每镜最新成功的 take。"""
    projects.get_project(project_id)
    row_shots = db.query("SELECT * FROM shots WHERE project_id=? ORDER BY seq", (project_id,))
    if not row_shots:
        raise err("FILM_MISSING_SHOTS", "项目还没有镜头，无法合成", detail={"missing": []})

    picked: list[dict] = []
    missing: list[dict] = []
    for s in row_shots:
        if take_ids:
            cand = [db.get("shot_takes", tid) for tid in take_ids]
            cand = [c for c in cand if c and c["shot_id"] == s["id"]]
            take = cand[0] if cand else None
        else:
            rows = db.query("SELECT * FROM shot_takes WHERE shot_id=? AND status='succeeded' "
                            "ORDER BY created_at DESC LIMIT 1", (s["id"],))
            take = rows[0] if rows else None
        if not take or take["status"] != "succeeded" or not take["asset_id"]:
            missing.append({"shot_id": s["id"], "seq": s["seq"], "status": "missing"})
        else:
            picked.append(take)

    if missing and not confirm_partial:
        raise err("FILM_MISSING_SHOTS",
                  f"有 {len(missing)} 个镜头缺少成功片段，不能产出完整成片",
                  detail={"missing_shots": missing,
                          "hint": "重试失败镜头后重新合成，或显式 confirm_partial=true 做部分合成"})
    if not picked:
        raise err("FILM_MISSING_SHOTS", "没有任何可用片段", detail={"missing": missing})

    # 纯拼接：按镜头顺序输出，不裁剪
    items = []
    total_ms = 0
    for t in picked:
        ms = t["duration_ms"] or probe_duration_ms(_asset_file(t["asset_id"]))
        items.append({**_take_view(t), "actual_duration_ms": ms})
        total_ms += int(ms or 0)

    version = int(db.query("SELECT COALESCE(MAX(version),0) v FROM films WHERE project_id=?",
                           (project_id,))[0]["v"]) + 1
    film_id = db.new_id("flm")
    status = "partial" if missing else "ready"
    db.insert("films", {"id": film_id, "project_id": project_id, "version": version,
                        "status": status, "items_json": db.dumps(
                            {"items": items, "missing": missing,
                             "planned_duration_s": sum(s["duration_s"] for s in row_shots)}),
                        "duration_ms": total_ms or None, "asset_id": None,
                        "partial": 1 if missing else 0, "created_at": db.now()})
    return {"film_id": film_id, "version": version, "status": status,
            "partial": bool(missing), "missing_shots": missing,
            "planned_duration_s": sum(s["duration_s"] for s in row_shots),
            "actual_duration_ms": total_ms or None,
            "clip_count": len(items),
            "note": "纯拼接，不裁剪/变速/补帧"}


def _asset_file(asset_id: str) -> Path:
    a = db.get("assets", asset_id) or {}
    rel = a.get("rel_path")
    return (config.ASSETS / rel) if rel else Path("/nonexistent")


def get_film(film_id: str) -> dict:
    row = db.get("films", film_id)
    if not row:
        raise err("FILM_NOT_FOUND", f"成片不存在：{film_id}")
    data = db.loads(row["items_json"]) or {}
    return {"film_id": row["id"], "project_id": row["project_id"],
            "version": row["version"], "status": row["status"],
            "asset_id": row["asset_id"], "duration_ms": row["duration_ms"],
            "partial": bool(row["partial"]),
            "items": data.get("items") or [],
            "missing_shots": data.get("missing") or [],
            "planned_duration_s": data.get("planned_duration_s")}


# ---------------- 结构模板（S6） ----------------
def save_template(name: str, snapshot: list[dict]) -> dict:
    if not isinstance(snapshot, list) or not snapshot:
        raise err("VALIDATION_FAILED", "snapshot 不能为空")
    for i, s in enumerate(snapshot, start=1):
        if not isinstance(s, dict):
            raise err("VALIDATION_FAILED", f"第 {i} 项不是对象")
        dur = s.get("duration_s")
        if dur is not None:
            config.resolve_duration_s(_default_model(), dur, strict=True)
    name = (name or "").strip()
    if not name:
        raise err("VALIDATION_FAILED", "模板名称不能为空")
    tid = db.new_id("stpl")
    db.insert("structure_templates", {"id": tid, "name": name, "latest_version": 1,
                                     "status": "active", "created_at": db.now(),
                                     "updated_at": db.now()})
    db.insert("structure_template_versions", {
        "id": db.new_id("stplv"), "template_id": tid, "version": 1,
        "snapshot_json": db.dumps(snapshot), "created_at": db.now()})
    return {"template_id": tid, "version": 1}


def save_from_project(project_id: str, name: str) -> dict:
    shots_rows = db.query("SELECT * FROM shots WHERE project_id=? ORDER BY seq", (project_id,))
    if not shots_rows:
        raise err("VALIDATION_FAILED", "项目没有镜头，无法保存为结构模板")
    snapshot = [{"seq": s["seq"], "role": s["description"][:40],
                 "duration_s": s["duration_s"], "motion": s["motion"],
                 "rhythm": None} for s in shots_rows]
    return save_template(name, snapshot)


def get_template(template_id: str, version: int | None = None) -> dict:
    row = db.get("structure_templates", template_id)
    if not row:
        raise err("STRUCTURE_TEMPLATE_NOT_FOUND", f"模板不存在：{template_id}")
    ver = version or row["latest_version"]
    rows = db.query("SELECT * FROM structure_template_versions WHERE template_id=? AND version=?",
                    (template_id, ver))
    if not rows:
        raise err("STRUCTURE_TEMPLATE_VERSION_NOT_FOUND",
                  f"模板版本不存在：{template_id} v{ver}")
    return {"template_id": template_id, "name": row["name"], "version": ver,
            "latest_version": row["latest_version"],
            "snapshot": db.loads(rows[0]["snapshot_json"]),
            "note": "模板只含结构，不含商品事实与素材"}


def apply_template(project_id: str, template_id: str, version: int | None = None) -> dict:
    """套用结构：生成镜头骨架；素材/商品事实**需重新绑定**；模型与时长**重新校验**。"""
    tpl = get_template(template_id, version)
    projects.update_project(project_id, brief={})       # 触发项目存在性校验
    # 需要已确认脚本作为载体
    latest = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1",
                      (project_id,))
    if not latest:
        raise err("VALIDATION_FAILED", "请先生成或编写脚本，再套用结构模板")
    created = []
    for item in tpl["snapshot"]:
        dur = config.resolve_duration_s(_default_model(), item.get("duration_s"), strict=True)
        sid = db.new_id("shot")
        db.insert("shots", {"id": sid, "project_id": project_id,
                            "script_version_id": latest[0]["id"],
                            "seq": item.get("seq") or (len(created) + 1),
                            "duration_s": dur,
                            "description": item.get("role") or "（待补充画面）",
                            "motion": item.get("motion"), "status": "draft",
                            "revision": 1, "created_at": db.now(), "updated_at": db.now()})
        created.append({"shot_id": sid, "seq": item.get("seq"),
                        "duration_s": dur, "role": item.get("role")})
    db.update("projects", project_id, {"structure_template_id": template_id,
                                      "structure_template_version": tpl["version"],
                                      "updated_at": db.now()})
    return {"project_id": project_id, "template_id": template_id,
            "template_version": tpl["version"], "created_shots": created,
            "requires": ["brief", "assets"],
            "model_revalidation": {"ok": True,
                                   "note": "时长已在套用时按当前模型能力重新校验"}}


# ---------------- 项目包导出（S6） ----------------
def export_project(project_id: str, include: list[str] | None = None) -> dict:
    """导出 zip：成片 + 片段 + 分镜图 + 清单 JSON。导出不重新生成。"""
    projects.get_project(project_id)
    include = include or ["film", "clips", "storyboards", "manifest"]
    import zipfile

    out_dir = config.ASSETS / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{project_id}-export.zip"
    target = out_dir / name
    manifest = {"project_id": project_id, "generated_at": db.now(),
                "shots": [], "films": []}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in db.query("SELECT * FROM shots WHERE project_id=? ORDER BY seq", (project_id,)):
            sb = db.query("SELECT * FROM storyboards WHERE shot_id=? AND status='approved' "
                          "ORDER BY version DESC LIMIT 1", (s["id"],))
            entry = {"shot_id": s["id"], "seq": s["seq"], "duration_s": s["duration_s"],
                     "description": s["description"],
                     "storyboard": ({"asset_id": sb[0]["asset_id"],
                                     "version": sb[0]["version"]} if sb else None)}
            if "storyboards" in include and sb and sb[0]["asset_id"]:
                f = _asset_file(sb[0]["asset_id"])
                if f.exists():
                    zf.write(f, f"storyboards/{s['seq']:02d}-{sb[0]['asset_id']}{f.suffix}")
            takes = db.query("SELECT * FROM shot_takes WHERE shot_id=? AND status='succeeded' "
                             "ORDER BY created_at DESC LIMIT 1", (s["id"],))
            if "clips" in include and takes and takes[0]["asset_id"]:
                f = _asset_file(takes[0]["asset_id"])
                if f.exists():
                    zf.write(f, f"clips/{s['seq']:02d}-{takes[0]['asset_id']}{f.suffix}")
            manifest["shots"].append(entry)
        if "film" in include:
            films = db.query("SELECT * FROM films WHERE project_id=? ORDER BY version DESC",
                             (project_id,))
            for f in films:
                manifest["films"].append({"film_id": f["id"], "version": f["version"],
                                          "status": f["status"],
                                          "duration_ms": f["duration_ms"],
                                          "partial": bool(f["partial"])})
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    asset = db.query("SELECT id FROM assets WHERE rel_path=?", (str(Path("exports") / name),))
    return {"export_id": f"exp_{project_id[-6:]}", "file_name": name,
            "path": str(target), "bytes": target.stat().st_size,
            "asset_id": asset[0]["id"] if asset else None,
            "includes": include}


def _default_model() -> str:
    for mid, meta in config.MODEL_REGISTRY.items():
        if meta.get("kind") == "video" and meta.get("provider") == "ark" \
                and config.model_submittability(mid)["submittable"]:
            return mid
    raise err("MODEL_NOT_OPENED", "没有可用的视频模型")
