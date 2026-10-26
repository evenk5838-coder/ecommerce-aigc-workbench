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
    """探测实际时长（毫秒）。优先 imageio_ffmpeg 元数据，其次 ffprobe，最后解析 ffmpeg 输出。

    都无法获取时返回 None（**如实标注未知，不猜**）。
    """
    if not path.exists():
        return None
    # 1) imageio_ffmpeg 元数据
    try:
        import imageio_ffmpeg
        gen = imageio_ffmpeg.read_frames(str(path))
        meta = next(gen)
        gen.close()
        dur = meta.get("duration")
        if dur:
            return int(float(dur) * 1000)
    except Exception:  # noqa: BLE001
        pass
    # 2) ffprobe
    exe = _ffprobe()
    if exe and Path(exe).exists():
        try:
            out = subprocess.run(
                [exe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=20)
            val = (out.stdout or "").strip()
            if val:
                return int(float(val) * 1000)
        except Exception:  # noqa: BLE001
            pass
    # 3) 解析 ffmpeg -i 输出
    ff = _ffmpeg()
    if ff:
        try:
            r = subprocess.run([ff, "-i", str(path)], capture_output=True, timeout=30)
            text = (r.stderr or b"").decode("utf-8", "ignore")
            import re
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
            if m:
                h, mi, s2 = int(m.group(1)), int(m.group(2)), float(m.group(3))
                return int((h * 3600 + mi * 60 + s2) * 1000)
        except Exception:  # noqa: BLE001
            pass
    return None


def concat_clips(paths: list[Path], out_path: Path) -> tuple[bool, str | None]:
    """ffmpeg 纯拼接（顺序，不裁剪/变速/补帧）。

    优先 `-c copy`（无损快速）；编码不一致时回退统一转码。
    """
    ff = _ffmpeg()
    if not ff:
        return False, "ffmpeg_not_found"
    if not paths:
        return False, "no_clips"
    list_file = out_path.with_suffix(".txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("".join(f"file '{p}'\n" for p in paths), encoding="utf-8")
    attempts = (
        ["-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
        ["-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", str(out_path)],
    )
    last = None
    try:
        for args in attempts:
            try:
                r = subprocess.run([ff, *args], capture_output=True, timeout=600)
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                continue
            if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                return True, None
            last = (r.stderr or b"")[-400:].decode("utf-8", "ignore")
        return False, last
    finally:
        list_file.unlink(missing_ok=True)


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
            confirm_partial: bool = False, source_batch: str | None = None) -> dict:
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
    planned_s = sum(s["duration_s"] for s in row_shots)

    # ---- 真实拼接（纯拼接，不裁剪）----
    clip_paths = [_asset_file(t["asset_id"]) for t in picked]
    out_tmp = config.ASSETS / "films" / f"{film_id}.mp4"
    ok, err_detail = concat_clips([p for p in clip_paths if p.exists()], out_tmp)
    asset_id = None
    concat_error = None
    if ok:
        from . import storage
        asset = storage.save_bytes(out_tmp.read_bytes(), "video", "assembly",
                                   meta={"film_id": film_id, "version": version,
                                         "clips": [t["id"] for t in picked],
                                         "no_trim": True})
        asset_id = asset["id"]
        probed = probe_duration_ms(_asset_file(asset_id))
        if probed:
            total_ms = probed
        out_tmp.unlink(missing_ok=True)
    else:
        status = "failed"
        concat_error = (err_detail or "")[:300]

    db.insert("films", {"id": film_id, "project_id": project_id, "version": version,
                        "source_batch": source_batch,
                        "status": status, "items_json": db.dumps(
                            {"items": items, "missing": missing,
                             "planned_duration_s": planned_s,
                             "concat_error": concat_error}),
                        "duration_ms": total_ms or None, "asset_id": asset_id,
                        "partial": 1 if missing else 0, "created_at": db.now()})
    if status == "failed":
        raise err("ASSEMBLY_FAILED", "成片拼接失败（片段或编码问题）",
                  detail={"film_id": film_id, "error": concat_error,
                          "film_status": "failed"})
    return {"film_id": film_id, "version": version, "status": status,
            "partial": bool(missing), "missing_shots": missing,
            "planned_duration_s": planned_s,
            "actual_duration_ms": total_ms or None,
            "asset_id": asset_id, "clip_count": len(items),
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
    """导出 zip：**成片 MP4 + 片段 + 分镜图 + manifest.json**，并登记可下载入口。

    导出不重新生成；文件缺失时在 manifest 里如实标记，不伪造。
    """
    projects.get_project(project_id)
    include = include or ["film", "clips", "storyboards", "manifest"]
    import zipfile

    export_id = db.new_id("exp")
    out_dir = config.ASSETS / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{project_id}-{export_id[-6:]}.zip"
    target = out_dir / file_name

    manifest = {"project_id": project_id, "export_id": export_id,
                "generated_at": db.now(), "includes": include,
                "films": [], "shots": [], "missing": []}

    shot_rows = db.query("SELECT * FROM shots WHERE project_id=? ORDER BY seq", (project_id,))
    film_rows = (db.query("SELECT * FROM films WHERE project_id=? ORDER BY version DESC",
                          (project_id,)) if _has("films") else [])
    any_film = False

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1) 成片 MP4
        if "film" in include:
            for f in film_rows:
                if not f.get("asset_id"):
                    manifest["missing"].append({"kind": "film", "film_id": f["id"],
                                                "reason": "no_asset"})
                    continue
                path = _asset_file(f["asset_id"])
                if path.exists():
                    zf.write(path, f"films/v{f['version']}.mp4")
                    manifest["films"].append({"film_id": f["id"], "version": f["version"],
                                              "status": f["status"],
                                              "duration_ms": f["duration_ms"],
                                              "planned_duration_s": (db.loads(f["items_json"]) or {})
                                              .get("planned_duration_s"),
                                              "partial": bool(f["partial"]),
                                              "file": f"films/v{f['version']}.mp4"})
                    any_film = True
                else:
                    manifest["missing"].append({"kind": "film", "film_id": f["id"],
                                                "reason": "file_not_found"})

        # 2) 逐镜：分镜图 + 片段
        for s in shot_rows:
            entry = {"shot_id": s["id"], "seq": s["seq"], "duration_s": s["duration_s"],
                     "description": s["description"], "storyboard": None, "clip": None}
            sb = db.query("SELECT * FROM storyboards WHERE shot_id=? AND status='approved' "
                          "ORDER BY version DESC LIMIT 1", (s["id"],))
            if "storyboards" in include and sb and sb[0]["asset_id"]:
                f = _asset_file(sb[0]["asset_id"])
                if f.exists():
                    name = f"storyboards/{s['seq']:02d}-v{sb[0]['version']}{f.suffix}"
                    zf.write(f, name)
                    entry["storyboard"] = {"version": sb[0]["version"], "file": name}
                else:
                    manifest["missing"].append({"kind": "storyboard", "shot_id": s["id"],
                                                "reason": "file_not_found"})
            takes = db.query("SELECT * FROM shot_takes WHERE shot_id=? AND status='succeeded' "
                             "ORDER BY created_at DESC LIMIT 1", (s["id"],))
            if "clips" in include and takes and takes[0]["asset_id"]:
                f = _asset_file(takes[0]["asset_id"])
                if f.exists():
                    name = f"clips/{s['seq']:02d}-{takes[0]['id'][-8:]}{f.suffix}"
                    zf.write(f, name)
                    entry["clip"] = {"take_id": takes[0]["id"],
                                     "duration_ms": takes[0]["duration_ms"], "file": name}
                else:
                    manifest["missing"].append({"kind": "clip", "shot_id": s["id"],
                                                "reason": "file_not_found"})
            manifest["shots"].append(entry)

        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    rel = str(Path("exports") / file_name)
    db.insert("project_exports", {"id": export_id, "project_id": project_id,
                                  "file_name": file_name, "rel_path": rel,
                                  "bytes": target.stat().st_size,
                                  "includes_json": db.dumps(include),
                                  "created_at": db.now()})
    return {"export_id": export_id, "project_id": project_id, "file_name": file_name,
            "bytes": target.stat().st_size, "includes": include,
            "download_url": f"/api/exports/{export_id}/file",
            "films_included": len(manifest["films"]),
            "clips_included": sum(1 for s in manifest["shots"] if s.get("clip")),
            "storyboards_included": sum(1 for s in manifest["shots"] if s.get("storyboard")),
            "missing": manifest["missing"],
            "note": "导出不重新生成；缺失项已在 manifest.missing 标注"}


def export_path(export_id: str) -> tuple[Path, dict]:
    row = db.get("project_exports", export_id)
    if not row:
        raise err("EXPORT_NOT_FOUND", f"导出不存在：{export_id}")
    path = config.ASSETS / row["rel_path"]
    if not path.exists():
        raise err("EXPORT_NOT_FOUND", "导出文件已不存在，请重新导出")
    return path, row


def _has(table: str) -> bool:
    return bool(db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                         (table,)))


def _default_model() -> str:
    for mid, meta in config.MODEL_REGISTRY.items():
        if meta.get("kind") == "video" and meta.get("provider") == "ark" \
                and config.model_submittability(mid)["submittable"]:
            return mid
    raise err("MODEL_NOT_OPENED", "没有可用的视频模型")
