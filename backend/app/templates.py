"""单次创作模板：种子数据、字段校验、提示词组装、版本快照（v0.7）。

设计约束（对应 PRD v0.7 §7）：
- 模板是**单次创作**，不是多步骤编排。
- `template_id` 语义稳定 + `template_version` 整数递增；版本发布后**不可原地修改**，
  任何变更必须新增版本；`template_versions.snapshot_json` 为不可变快照。
- 任务只记录 `template_id + template_version`，历史任务按当时快照展示，不随升版变化。
- 首版人工维护，不自动抓取热点。
"""
from __future__ import annotations

from typing import Any

from . import db
from .errors import err

# 商品保真基座：所有带参考图的模板共用，禁止暗示数量一致性保证
PRESERVE = (
    "以参考图中实际出现的商品为准，保留所有商品及其数量、相对位置、形状比例、材质、颜色、"
    "品牌标识和包装文字，不增加或删除商品。"
)
CONCEPT_NOTICE = "此为创意概念图，不代表真实商品外观。"

_PROTECT_FIELDS = [
    {"key": "keep_features", "label": "需保留特征（可选）", "type": "textarea", "required": False,
     "placeholder": "例：瓶盖金色、标签文字不可改、正面标志位"},
    {"key": "forbidden_elements", "label": "禁用元素（可选）", "type": "textarea", "required": False,
     "placeholder": "例：不要出现人物、不要添加其他商品、不要加促销贴纸"},
]

_REQUIRED_REFERENCE = [
    {"role": "product_reference", "kind": "image", "required": True, "max_count": 1,
     "label": "商品图"},
]

SEED_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_id": "tpl_white_bg",
        "version": 1,
        "category": "work",
        "kind": "image",
        "name": "商品白底背景",
        "description": "把商品图换成纯白棚拍背景，适合平台主图、白底图。要求提供商品参考图。",
        "effect_sample": {"asset_id": None, "note": "发布时补真实样例"},
        "required_assets": _REQUIRED_REFERENCE,
        "fields": [
            {"key": "product_name", "label": "商品名称", "type": "text", "required": False,
             "placeholder": "例：焕亮精华液 30ml"},
            {"key": "category", "label": "品类", "type": "select", "required": False,
             "options": ["美妆", "食品", "3C", "服饰", "家居", "其他"]},
            *_PROTECT_FIELDS,
            {"key": "extra", "label": "补充描述（可选）", "type": "textarea", "required": False,
             "placeholder": "例：保留原有留白，不要加文字"},
        ],
        "default_params": {"generation_mode": "image_to_image", "size": "2048x2048", "purpose": "white"},
        "compatible_models": {"kind": "image", "input_modes": ["image_to_image"]},
        "prompt": {
            "preset": PRESERVE + "将背景替换为干净的纯白背景，柔和棚拍光线，自然接触阴影，商品完整清晰，适合电商商品展示。",
            "field_order": ["keep_features", "forbidden_elements", "extra"],
        },
        "status": "active",
    },
    {
        "template_id": "tpl_scene_bg",
        "version": 1,
        "category": "work",
        "kind": "image",
        "name": "商品场景换背景",
        "description": "把商品图放进生活场景，适合氛围图、详情页场景图。要求提供商品参考图。",
        "effect_sample": {"asset_id": None, "note": "发布时补真实样例"},
        "required_assets": _REQUIRED_REFERENCE,
        "fields": [
            {"key": "product_name", "label": "商品名称", "type": "text", "required": False,
             "placeholder": "例：焕亮精华液 30ml"},
            {"key": "scene", "label": "目标场景", "type": "select", "required": True,
             "options": ["大理石台面", "木质桌面", "简约居家", "户外自然", "自定义"],
             "allow_custom": True, "custom_key": "scene_custom"},
            *_PROTECT_FIELDS,
            {"key": "extra", "label": "补充描述（可选）", "type": "textarea", "required": False},
        ],
        "default_params": {"generation_mode": "image_to_image", "size": "2048x2048", "purpose": "scene"},
        "compatible_models": {"kind": "image", "input_modes": ["image_to_image"]},
        "prompt": {
            "preset": PRESERVE + "将背景替换为{scene}，柔和自然光，背景不遮挡商品，突出商品质感，构图留白。",
            "scene_field": "scene",
            "scene_custom_key": "scene_custom",
            "field_order": ["keep_features", "forbidden_elements", "extra"],
        },
        "status": "active",
    },
    {
        "template_id": "tpl_product_motion_5s",
        "version": 1,
        "category": "work",
        "kind": "video",
        "name": "5 秒商品动态展示",
        "description": "以商品图为首帧生成 5 秒动态展示短片。要求提供商品首帧图。",
        "effect_sample": {"asset_id": None, "note": "发布时补真实样例"},
        "required_assets": [
            {"role": "first_frame", "kind": "image", "required": True, "max_count": 1,
             "label": "商品首帧图"},
        ],
        "fields": [
            {"key": "product_name", "label": "商品名称", "type": "text", "required": False},
            {"key": "motion", "label": "动态方式", "type": "select", "required": True,
             "options": ["缓慢旋转", "推近特写", "环绕展示", "自定义"],
             "allow_custom": True, "custom_key": "motion_custom"},
            {"key": "duration", "label": "时长（秒）", "type": "select", "required": True, "options": [5, 10]},
            {"key": "ratio", "label": "画面比例", "type": "select", "required": False,
             "options": ["9:16", "1:1"]},
            {"key": "keep_features", "label": "需保留特征（可选）", "type": "textarea", "required": False},
            {"key": "forbidden_elements", "label": "禁用元素（可选）", "type": "textarea", "required": False},
        ],
        "default_params": {"generation_mode": "image_to_video", "duration": 5, "ratio": "9:16",
                            "resolution": "720p", "generate_audio": False},
        "compatible_models": {"kind": "video", "input_modes": ["image_to_video"]},
        "prompt": {
            "preset": ("以首帧图中的商品为准，保持商品外形、数量、颜色、品牌标识与包装文字不变，"
                       "不增加或删除商品。{motion}，镜头平稳，光线自然，适合电商短视频。"),
            "scene_field": "motion",
            "scene_custom_key": "motion_custom",
            "field_order": ["keep_features", "forbidden_elements"],
        },
        "status": "active",
    },
    # ---- v2：修复“模板字段未生效”——新增 field_params 映射，v1 快照保持不变 ----
    {
        "template_id": "tpl_product_motion_5s",
        "version": 2,
        "category": "work",
        "kind": "video",
        "name": "5 秒商品动态展示",
        "description": "以商品图为首帧生成 5 秒动态展示短片。要求提供商品首帧图。",
        "effect_sample": {"asset_id": None, "note": "发布时补真实样例"},
        "required_assets": [
            {"role": "first_frame", "kind": "image", "required": True, "max_count": 1,
             "label": "商品首帧图"},
        ],
        "fields": [
            {"key": "product_name", "label": "商品名称", "type": "text", "required": False},
            {"key": "motion", "label": "动态方式", "type": "select", "required": True,
             "options": ["缓慢旋转", "推近特写", "环绕展示", "自定义"],
             "allow_custom": True, "custom_key": "motion_custom"},
            {"key": "duration", "label": "时长（秒）", "type": "select", "required": False,
             "options": [5, 10], "param_key": "duration",
             "note": "不填时使用模板默认 5 秒，避免选了 10 秒却静默用 5 秒"},
            {"key": "ratio", "label": "画面比例", "type": "select", "required": False,
             "options": ["9:16", "1:1"], "param_key": "ratio"},
            {"key": "keep_features", "label": "需保留特征（可选）", "type": "textarea", "required": False},
            {"key": "forbidden_elements", "label": "禁用元素（可选）", "type": "textarea", "required": False},
        ],
        "default_params": {"generation_mode": "image_to_video", "duration": 5, "ratio": "9:16",
                            "resolution": "720p", "generate_audio": False},
        # 模板字段 → 生成参数映射（优先级：显式 params > 此映射 > default_params）
        "field_params": {"duration": "duration", "ratio": "ratio"},
        "compatible_models": {"kind": "video", "input_modes": ["image_to_video"]},
        "prompt": {
            "preset": ("以首帧图中的商品为准，保持商品外形、数量、颜色、品牌标识与包装文字不变，"
                       "不增加或删除商品。{motion}，镜头平稳，光线自然，适合电商短视频。"),
            "scene_field": "motion",
            "scene_custom_key": "motion_custom",
            "field_order": ["keep_features", "forbidden_elements"],
        },
        "status": "active",
    },
    {
        "template_id": "tpl_season_theme",
        "version": 1,
        "category": "hot",
        "kind": "image",
        "name": "季节上新主题",
        "description": ("季节/节日主题氛围图。默认使用商品参考图；未提供参考图时进入"
                        "「创意概念图」模式，产物不代表真实商品。"),
        "effect_sample": {"asset_id": None, "note": "发布时补真实样例"},
        "required_assets": [
            {"role": "product_reference", "kind": "image", "required": False, "max_count": 1,
             "label": "商品图（建议提供）"},
        ],
        "fields": [
            {"key": "product_name", "label": "商品名称", "type": "text", "required": False},
            {"key": "theme", "label": "主题", "type": "select", "required": True,
             "options": ["春季上新", "夏季清凉", "秋季", "冬季", "节日", "自定义"],
             "allow_custom": True, "custom_key": "theme_custom"},
            {"key": "theme_copy", "label": "主题文案（可选）", "type": "text", "required": False,
             "placeholder": "例：焕新一季"},
            *_PROTECT_FIELDS,
        ],
        "default_params": {"generation_mode": "image_to_image", "size": "2048x2048", "purpose": "scene"},
        "compatible_models": {"kind": "image", "input_modes": ["text_to_image", "image_to_image"]},
        "prompt": {
            "preset": PRESERVE + "将背景与整体色调替换为{theme}，氛围自然，商品完整清晰，构图留白。",
            "concept_preset": ("生成一张{theme}主题的电商创意概念图，商品依据文字描述：{product_name}。"
                               + CONCEPT_NOTICE),
            "scene_field": "theme",
            "scene_custom_key": "theme_custom",
            "field_order": ["theme_copy", "keep_features", "forbidden_elements"],
        },
        "status": "active",
    },
]

_FIELD_LABELS = {"keep_features": "需保留特征", "forbidden_elements": "禁用元素",
                 "extra": "补充描述", "theme_copy": "主题文案"}


# ---------- 播种 ----------
def seed() -> dict:
    """幂等播种：同 template_id+version 已存在则跳过（不覆盖已发布版本）。"""
    created, skipped = 0, 0
    for tpl in SEED_TEMPLATES:
        tid, ver = tpl["template_id"], int(tpl["version"])
        if db.query("SELECT 1 FROM template_versions WHERE template_id=? AND version=?", (tid, ver)):
            skipped += 1
            continue
        db.insert("template_versions", {
            "id": db.new_id("tplv"), "template_id": tid, "version": ver,
            "snapshot_json": db.dumps(tpl), "status": tpl.get("status", "active"),
            "created_at": db.now(),
        })
        row = db.query("SELECT id FROM templates WHERE template_id=?", (tid,))
        if row:
            db.update("templates", row[0]["id"], {"latest_version": ver,
                                                  "status": tpl.get("status", "active")})
        else:
            db.insert("templates", {
                "id": db.new_id("tpl"), "template_id": tid, "category": tpl["category"],
                "kind": tpl["kind"], "name": tpl["name"], "latest_version": ver,
                "status": tpl.get("status", "active"),
                "created_at": db.now(), "updated_at": db.now(),
            })
        created += 1
    return {"created": created, "skipped": skipped}


# ---------- 查询 ----------
def latest_version(template_id: str) -> int | None:
    rows = db.query(
        "SELECT MAX(version) v FROM template_versions WHERE template_id=? AND status='active'",
        (template_id,),
    )
    return rows[0]["v"] if rows and rows[0]["v"] is not None else None


def snapshot(template_id: str, version: int | None = None) -> dict | None:
    ver = version or latest_version(template_id)
    if ver is None:
        return None
    rows = db.query(
        "SELECT snapshot_json, status FROM template_versions WHERE template_id=? AND version=?",
        (template_id, ver),
    )
    if not rows:
        return None
    return {**db.loads(rows[0]["snapshot_json"]), "_stored_status": rows[0]["status"]}


def list_templates(kind: str | None = None, category: str | None = None) -> list[dict]:
    out = []
    for row in db.query("SELECT * FROM templates ORDER BY category, template_id"):
        if kind and row["kind"] != kind:
            continue
        if category and row["category"] != category:
            continue
        snap = snapshot(row["template_id"], row["latest_version"]) or {}
        out.append({**row, "snapshot": snap})
    return out


def detail(template_id: str, version: int | None = None) -> dict:
    row = db.query("SELECT * FROM templates WHERE template_id=?", (template_id,))
    if not row:
        raise err("TEMPLATE_NOT_FOUND", f"模板不存在：{template_id}")
    snap = snapshot(template_id, version)
    if not snap:
        raise err("TEMPLATE_VERSION_NOT_FOUND",
                  f"模板版本不存在：{template_id} v{version}", detail={"template_id": template_id})
    versions = db.query(
        "SELECT version, status, created_at FROM template_versions WHERE template_id=? ORDER BY version",
        (template_id,),
    )
    return {**row[0], "requested_version": version or row[0]["latest_version"],
            "snapshot": snap, "versions": versions}


# ---------- 校验与组装 ----------
def _pick_custom(fields: dict, key: str, custom_key: str | None, default: str = "") -> str:
    val = str(fields.get(key) or "").strip()
    if val == "自定义" and custom_key:
        val = str(fields.get(custom_key) or "").strip()
        if not val:
            raise err("TEMPLATE_FIELD_INVALID", f"选择「自定义」时必须填写 {custom_key}",
                      detail={"field": custom_key})
    return val or default


def validate_fields(snap: dict, fields: dict, has_reference: bool) -> dict:
    """按模板字段定义校验；返回清洗后的字段值（含关联的自定义字段）。

    修复要点：
    - `scene_custom` / `motion_custom` / `theme_custom` 等由 `custom_key` 声明的字段
      **必须被保留**，否则选“自定义”时下游 _pick_custom 读不到内容。
    - 选“自定义”但内容为空 → TEMPLATE_FIELD_REQUIRED（指向 custom_key）。
    - 未选“自定义”时，对应自定义值**不写入**清洗结果（不污染提示词）。
    """
    clean: dict = {}
    for f in snap.get("fields", []):
        key = f["key"]
        val = fields.get(key)
        if f.get("type") == "select" and val is not None and str(val).strip():
            allowed = [str(o) for o in f.get("options", [])]
            if str(val) not in allowed:
                raise err("TEMPLATE_FIELD_INVALID",
                          f"{f['label']} 只能为 {allowed}",
                          detail={"field": key, "allowed": allowed, "got": val})
        if f["required"] and not str(val or "").strip():
            raise err("TEMPLATE_FIELD_REQUIRED", f"请填写{f['label']}", detail={"field": key})
        if val not in (None, ""):
            clean[key] = val

        # ---- 关联自定义字段 ----
        custom_key = f.get("custom_key")
        if custom_key:
            cval = str(fields.get(custom_key) or "").strip()
            if str(val or "").strip() == "自定义":
                if not cval:
                    raise err("TEMPLATE_FIELD_REQUIRED",
                              f"{f['label']}选择「自定义」时必须填写具体内容",
                              detail={"field": custom_key, "parent": key})
                clean[custom_key] = cval
            # 未选择“自定义”：cval 被丢弃，不进入提示词与任务快照

    for req in snap.get("required_assets", []):
        if req.get("required") and not has_reference:
            raise err("REFERENCE_IMAGE_REQUIRED",
                      f"模板「{snap['name']}」要求提供{req.get('label', '素材')}",
                      detail={"template_id": snap["template_id"], "role": req.get("role")})

    # 概念图路径必须至少有商品文字描述，避免生成与真实商品无关的内容
    if snap["template_id"] == "tpl_season_theme" and not has_reference:
        if not str(clean.get("product_name") or "").strip():
            raise err("TEMPLATE_FIELD_REQUIRED",
                      "未提供商品参考图时，必须填写商品名称（将生成创意概念图）",
                      detail={"field": "product_name", "concept_mode": True})
    return clean


def _field_params(snap: dict, clean: dict) -> dict:
    """模板字段 → 生成参数映射（如 duration / ratio）。"""
    mapping = snap.get("field_params") or {}
    out: dict = {}
    for field_key, param_key in mapping.items():
        val = clean.get(field_key)
        if val in (None, ""):
            continue
        if param_key == "duration":
            try:
                out[param_key] = int(val)
            except (TypeError, ValueError):
                raise err("TEMPLATE_FIELD_INVALID", f"时长必须为数字，收到：{val}",
                          detail={"field": field_key}) from None
        else:
            out[param_key] = str(val)
    return out


def compose_prompt(snap: dict, fields: dict, has_reference: bool) -> tuple[str, bool]:
    """组装提示词；返回 (prompt, concept_mode)。"""
    spec = snap.get("prompt", {})
    concept = bool(snap["template_id"] == "tpl_season_theme" and not has_reference)

    preset = spec.get("concept_preset") if concept else spec.get("preset")
    preset = preset or spec.get("preset") or ""
    scene_val = ""
    if spec.get("scene_field"):
        scene_val = _pick_custom(fields, spec["scene_field"], spec.get("scene_custom_key"))
    try:
        text = preset.format(**{spec.get("scene_field") or "_": scene_val,
                                "scene": scene_val, "motion": scene_val, "theme": scene_val,
                                "product_name": str(fields.get("product_name") or "该商品")})
    except (KeyError, IndexError):
        text = preset

    parts = [text.strip()]
    for key in spec.get("field_order", []):
        val = str(fields.get(key) or "").strip()
        if val:
            parts.append(f"{_FIELD_LABELS.get(key, key)}：{val}")
    if concept:
        parts.append(CONCEPT_NOTICE)
    return " ".join(p for p in parts if p), concept


def resolve_for_task(template_id: str, template_version: int | None, fields: dict,
                     has_reference: bool, explicit_params: dict | None = None) -> dict:
    """任务解析（**纯函数，无副作用**）：供提交与费用预估共用。

    参数优先级：
        1. 调用方显式 params（最高）
        2. 模板字段映射（如 duration/ratio）
        3. 模板默认值 default_params（最低）
    例外：季节模板的 `generation_mode` 由**参考图存在性派生**（模板规则），
    否则会出现“概念图却要求参考图”的矛盾；被忽略的显式值会回显供排查。
    """
    snap = snapshot(template_id, template_version)
    if not snap:
        raise err("TEMPLATE_NOT_FOUND", f"模板或版本不存在：{template_id} v{template_version}")
    if snap.get("_stored_status") not in (None, "active"):
        raise err("TEMPLATE_DEPRECATED",
                  f"模板版本已停用：{template_id} v{snap.get('version')}",
                  detail={"status": snap.get("_stored_status")})
    clean = validate_fields(snap, fields or {}, has_reference)
    prompt, concept = compose_prompt(snap, clean, has_reference)

    explicit = dict(explicit_params or {})
    explicit_prompt = explicit.pop("prompt", None)
    explicit.pop("fields", None)
    warnings: list[dict] = []

    # 1) 默认值 → 2) 字段映射 → 3) 显式参数
    params: dict = dict(snap.get("default_params", {}))
    params.update(_field_params(snap, clean))
    params.update(explicit)

    # 4) 概念图路径：模式由参考图派生
    if snap["template_id"] == "tpl_season_theme":
        derived = "image_to_image" if has_reference else "text_to_image"
        if str(params.get("generation_mode") or "") not in ("", derived):
            warnings.append({"code": "GENERATION_MODE_DERIVED",
                             "message": f"季节模板的生成方式由参考图存在性决定，已改为 {derived}",
                             "ignored_value": params.get("generation_mode"),
                             "applied_value": derived})
        params["generation_mode"] = derived
        if concept:
            params["concept_mode"] = True

    return {
        "template_id": snap["template_id"],
        "template_version": int(snap["version"]),
        "template_name": snap["name"],
        "fields": clean,
        "prompt": prompt,
        "explicit_prompt": explicit_prompt,
        "concept_mode": concept,
        "params": params,
        "default_params": snap.get("default_params", {}),
        "compatible_models": snap.get("compatible_models", {}),
        "warnings": warnings,
    }


def model_compatible(snap_or_compat: dict, model_id: str, capabilities: dict) -> bool:
    """模板与模型兼容性：kind + input_modes 交集。"""
    compat = snap_or_compat.get("compatible_models", snap_or_compat)
    if not compat:
        return True
    if compat.get("kind") and capabilities.get("kind") != compat.get("kind"):
        return False
    need = compat.get("input_modes") or []
    have = capabilities.get("input_modes") or []
    return bool(set(need) & set(have)) if need else True
