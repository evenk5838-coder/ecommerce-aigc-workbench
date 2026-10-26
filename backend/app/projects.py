"""S2：项目 / 需求信息（brief）/ 脚本版本。

设计约束（PRD v0.10 §2.1–§2.3、技术方案 v6 §1.3、接口契约 v0.10 §3）：
- **脚本只追加不覆盖**：任何编辑都产生新 `version`，旧版本可回滚查看。
- **AI 脚本生成由用户主动触发**；助手不可用时返回 `assistant_unavailable=true` 并给提示，
  **不报 5xx、不阻断手工编辑**。
- **确认绑定 `script_id + version`**；改 brief / 改脚本后确认失效（返回 stale 标记，不自动改状态）。
- 不在此层调用图片/视频模型；不新建任务。
"""
from __future__ import annotations

import json
import os
from typing import Any

from . import db
from .errors import err

BRIEF_FIELDS = ("subject", "quantity", "selling_points", "forbidden_elements",
                "target_platform", "target_duration_s", "style", "notes")
PROJECT_STATUS = ("draft", "scripting", "script_confirmed", "archived")


# ---------------- 项目 ----------------
def create_project(name: str, brief: dict | None = None,
                   structure_template_id: str | None = None,
                   structure_template_version: int | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise err("VALIDATION_FAILED", "项目名称不能为空", detail={"field": "name"})
    clean = _clean_brief(brief or {})
    pid = db.new_id("prj")
    row = {"id": pid, "name": name, "brief_json": db.dumps(clean), "status": "draft",
           "structure_template_id": structure_template_id,
           "structure_template_version": structure_template_version,
           "created_at": db.now(), "updated_at": db.now()}
    db.insert("projects", row)
    return _project_view(row)


def get_project(project_id: str) -> dict:
    row = db.get("projects", project_id)
    if not row:
        raise err("PROJECT_NOT_FOUND", f"项目不存在：{project_id}",
                  detail={"project_id": project_id})
    return row


def project_detail(project_id: str) -> dict:
    row = get_project(project_id)
    scripts = list_scripts(project_id)
    latest = scripts[0] if scripts else None
    return {**_project_view(row), "script_count": len(scripts),
            "latest_script": _script_summary(latest) if latest else None}


def list_projects(limit: int = 20) -> list[dict]:
    rows = db.query("SELECT * FROM projects ORDER BY updated_at DESC LIMIT ?",
                    (max(1, min(limit, 100)),))
    out = []
    for r in rows:
        scripts = db.query("SELECT COUNT(*) c FROM script_versions WHERE project_id=?",
                           (r["id"],))[0]["c"]
        out.append({**_project_view(r), "script_count": scripts})
    return out


def update_project(project_id: str, name: str | None = None,
                   brief: dict | None = None) -> dict:
    row = get_project(project_id)
    patch: dict[str, Any] = {"updated_at": db.now()}
    if name is not None:
        name = name.strip()
        if not name:
            raise err("VALIDATION_FAILED", "项目名称不能为空", detail={"field": "name"})
        patch["name"] = name
    stale: list[str] = []
    if brief is not None:
        old = db.loads(row["brief_json"]) or {}
        new = {**old, **_clean_brief(brief)}
        if new != old:
            patch["brief_json"] = db.dumps(new)
            # brief 变化 → **持久化失效**下游确认（不删除历史确认记录）
            from . import director
            affected = director.invalidate_after_upstream_change(
                project_id=project_id, change="brief")
            stale = ["script_confirm"]
            if affected.get("shot_plan"):
                stale.append("shot_plan_confirm")
    db.update("projects", project_id, patch)
    return {**_project_view(get_project(project_id)), "stale": stale}


def _clean_brief(brief: dict) -> dict:
    if not isinstance(brief, dict):
        raise err("VALIDATION_FAILED", "brief 必须是对象")
    unknown = [k for k in brief if k not in BRIEF_FIELDS]
    if unknown:
        raise err("VALIDATION_FAILED", f"brief 含未知字段：{unknown}",
                  detail={"allowed": list(BRIEF_FIELDS), "unknown": unknown})
    q = brief.get("quantity")
    if q is not None and (isinstance(q, bool) or not isinstance(q, int) or q <= 0):
        raise err("VALIDATION_FAILED", "quantity 必须为正整数",
                  detail={"field": "quantity", "got": q})
    return {k: v for k, v in brief.items() if v is not None}


def _project_view(row: dict) -> dict:
    return {"project_id": row["id"], "name": row["name"],
            "brief": db.loads(row.get("brief_json")) or {},
            "status": row.get("status"),
            "structure_template_id": row.get("structure_template_id"),
            "structure_template_version": row.get("structure_template_version"),
            "created_at": row.get("created_at"), "updated_at": row.get("updated_at")}


# ---------------- 脚本 ----------------
def _next_version(project_id: str) -> int:
    rows = db.query("SELECT COALESCE(MAX(version),0) v FROM script_versions WHERE project_id=?",
                    (project_id,))
    return int(rows[0]["v"]) + 1


def add_script(project_id: str, content: dict, source: str = "manual",
               session_id: str | None = None,
               base_version: int | None = None) -> dict:
    """新增脚本版本。

    **版本分配与并发校验在同一事务内完成**（BEGIN IMMEDIATE），避免两个请求拿到同一个
    version 或覆盖他人写入；`base_version` 指客户端所见的项目最新版本，不一致 → 409。
    """
    get_project(project_id)
    if not isinstance(content, dict):
        raise err("VALIDATION_FAILED", "脚本 content 必须是对象")
    sid = db.new_id("scr")
    with db.transaction() as c:
        row = c.execute("SELECT COALESCE(MAX(version),0) v FROM script_versions "
                        "WHERE project_id=?", (project_id,)).fetchone()
        latest = int(row["v"])
        if base_version is not None and int(base_version) != latest:
            raise err("SCRIPT_VERSION_CONFLICT",
                      f"脚本已更新：项目当前最新 v{latest}，提交 v{base_version}",
                      detail={"current_version": latest, "base_version": base_version})
        ver = latest + 1
        c.execute(
            "INSERT INTO script_versions(id, project_id, version, content_json, source, "
            "session_id, confirmed_at, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (sid, project_id, ver, db.dumps(content), source, session_id, None, db.now()))
        c.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?",
                  ("scripting", db.now(), project_id))
    return _script_view(db.get("script_versions", sid))


def list_scripts(project_id: str) -> list[dict]:
    get_project(project_id)
    rows = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC",
                    (project_id,))
    return [_script_view(r) for r in rows]


def get_script(script_id: str) -> dict:
    row = db.get("script_versions", script_id)
    if not row:
        raise err("SCRIPT_NOT_FOUND", f"脚本不存在：{script_id}",
                  detail={"script_id": script_id})
    return _script_view(row)


def edit_script(script_id: str, content: dict, base_version: int | None = None) -> dict:
    """手工编辑 → **新建版本**（不覆盖历史）。"""
    row = db.get("script_versions", script_id)
    if not row:
        raise err("SCRIPT_NOT_FOUND", f"脚本不存在：{script_id}")
    return add_script(row["project_id"], content, source="manual",
                      session_id=row.get("session_id"), base_version=base_version)


def confirm_script(script_id: str, confirm: bool = True) -> dict:
    """确认脚本 → **持久化确认记录**（绑定 script_id + version）。"""
    from . import director

    row = db.get("script_versions", script_id)
    if not row:
        raise err("SCRIPT_NOT_FOUND", f"脚本不存在：{script_id}")
    if not confirm:
        raise err("VALIDATION_FAILED", "确认动作需 confirm=true")
    now = db.now()
    cfm = director.confirm_object(row["project_id"], "script", script_id,
                                 int(row["version"]))
    db.update("script_versions", script_id, {"confirmed_at": now})
    db.update("projects", row["project_id"], {"status": "script_confirmed"})
    return {"script_id": script_id, "version": row["version"],
            "confirmed_at": now, "confirmation": cfm,
            "unlocked": ["storyboard_plan"]}


def _script_view(row: dict) -> dict:
    return {"script_id": row["id"], "project_id": row["project_id"],
            "version": row["version"], "content": db.loads(row.get("content_json")) or {},
            "source": row.get("source"), "confirmed_at": row.get("confirmed_at"),
            "created_at": row.get("created_at")}


def _script_summary(script: dict) -> dict:
    return {"script_id": script["script_id"], "version": script["version"],
            "source": script.get("source"), "confirmed_at": script.get("confirmed_at")}


# ---------------- AI 脚本生成（用户主动触发） ----------------
SCRIPT_SYSTEM_PROMPT = (
    "你是电商短视频脚本助手。根据商品资料写一个**简单视频脚本**，"
    "输出 JSON：{\"summary\": \"3-6 句整体叙事\", \"shots\": [{\"seq\": 1, \"hint\": \"一句话画面描述\"}]}。"
    "要求：只使用用户提供的商品事实，不虚构成分/功效/认证；不承诺效果；镜头数 2-6 个。"
)


def generate_script(project_id: str, instruction: str | None = None,
                    target_duration_s: int | None = None,
                    provider=None) -> dict:  # noqa: ANN001
    """brief → 脚本。**调用方必须先由用户点击触发**；本函数不写任务、不调图片/视频模型。"""
    from .assistant import provider as prov
    from .assistant.service import record_usage

    row = get_project(project_id)
    brief = db.loads(row.get("brief_json")) or {}
    if not brief.get("subject"):
        raise err("VALIDATION_FAILED", "请先填写商品名称（brief.subject）",
                  detail={"field": "subject"})

    p = provider or prov.get_provider()
    if not p.available():
        # 助手不可用：不报 5xx，返回可手工继续的提示
        return {"assistant_unavailable": True,
                "hint": "助手暂不可用，可先手工编写脚本并继续后续步骤",
                "project_id": project_id}

    payload = {"brief": brief, "instruction": instruction,
               "target_duration_s": target_duration_s}
    messages = [
        {"role": "system", "content": SCRIPT_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    res = p.chat(messages, [])
    content = _parse_script(res.reply)
    script = add_script(project_id, content, source="assistant")
    try:
        record_usage(None, "llm", provider=p.name, model=p.model,
                     input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                     tool_calls=0, note=f"script_generate:{project_id}")
    except Exception:  # noqa: BLE001  用量记录失败不影响结果
        pass
    return {"script_id": script["script_id"], "version": script["version"],
            "content": content, "source": "assistant",
            "llm_usage": {"input_tokens": res.input_tokens,
                          "output_tokens": res.output_tokens},
            "assistant_unavailable": False}


def _parse_script(text: str) -> dict:
    """解析模型输出；解析失败**明确降级**（原文保存，不编造镜头）。"""
    raw = (text or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("summary"), str):
                shots = data.get("shots")
                return {"summary": data["summary"],
                        "shots": shots if isinstance(shots, list) else [],
                        "parse_ok": True}
        except json.JSONDecodeError:
            pass
    return {"summary": raw, "shots": [], "parse_ok": False,
            "degraded_reason": "model_output_not_structured"}


# ---------------- 采用助手草案（**不调用模型**） ----------------
def adopt_draft(project_id: str, kind: str, content: dict,
                base_version: int | None = None) -> dict:
    """把助手产出的草案**落库**。仅写库，不产生任何模型调用。

    - kind="brief"  → 合并进项目 brief（不覆盖未提及字段）
    - kind="script" → 新增脚本版本（可带 base_version 做并发校验）
    """
    get_project(project_id)
    if kind == "brief":
        out = update_project(project_id, brief=content)
        return {"kind": "brief", "project_id": project_id, "brief": out["brief"],
                "stale": out["stale"], "llm_called": False}
    if kind == "script":
        script = add_script(project_id, content, source="assistant",
                            base_version=base_version)
        return {"kind": "script", "script_id": script["script_id"],
                "version": script["version"], "content": script["content"],
                "llm_called": False}
    raise err("VALIDATION_FAILED", f"未知草案类型：{kind}",
              detail={"allowed": ["brief", "script"]})
