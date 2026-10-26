"""创作空间路由（S2：项目 / brief / 脚本）。

只做 S2 范围；shots / scope / films / structure-templates 尚未实现（S3 起）。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import director, projects, scope, shots

router = APIRouter(prefix="/api", tags=["creative"])


class ProjectIn(BaseModel):
    name: str
    brief: dict | None = None
    structure_template_id: str | None = None
    structure_template_version: int | None = None


class ProjectPatch(BaseModel):
    name: str | None = None
    brief: dict | None = None


class ScriptGenIn(BaseModel):
    instruction: str | None = None
    target_duration_s: int | None = None


class ScriptEditIn(BaseModel):
    content: dict
    base_version: int | None = None


class ConfirmIn(BaseModel):
    confirm: bool = True


class DraftIn(BaseModel):
    kind: str                      # brief | script
    content: dict
    base_version: int | None = None


@router.post("/projects")
def create_project(body: ProjectIn):
    return projects.create_project(body.name, body.brief,
                                   body.structure_template_id,
                                   body.structure_template_version)


@router.get("/projects")
def list_projects(limit: int = 20):
    return {"items": projects.list_projects(limit)}


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    return projects.project_detail(project_id)


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch):
    return projects.update_project(project_id, body.name, body.brief)


@router.post("/projects/{project_id}/scripts")
def generate_script(project_id: str, body: ScriptGenIn | None = None):
    """AI 生成脚本（用户主动触发）；助手不可用时返回 assistant_unavailable=true。"""
    body = body or ScriptGenIn()
    return projects.generate_script(project_id, body.instruction, body.target_duration_s)


@router.post("/projects/{project_id}/scripts/manual")
def add_manual_script(project_id: str, body: ScriptEditIn):
    """手工新增脚本版本（助手不可用时的兜底路径）。"""
    script = projects.add_script(project_id, body.content, source="manual")
    return {"script_id": script["script_id"], "version": script["version"],
            "content": script["content"], "source": "manual"}


@router.get("/projects/{project_id}/scripts")
def list_scripts(project_id: str):
    return {"items": projects.list_scripts(project_id)}


@router.get("/scripts/{script_id}")
def get_script(script_id: str):
    return projects.get_script(script_id)


@router.patch("/scripts/{script_id}")
def edit_script(script_id: str, body: ScriptEditIn):
    return projects.edit_script(script_id, body.content, body.base_version)


@router.post("/scripts/{script_id}/confirm")
def confirm_script(script_id: str, body: ConfirmIn | None = None):
    return projects.confirm_script(script_id, bool((body or ConfirmIn()).confirm))


@router.get("/projects/{project_id}/director")
def director_readiness(project_id: str):
    """导演台就绪度：当前阶段 / 确认是否有效 / 失效原因 / 允许动作。**不调用模型**。"""
    return director.readiness(project_id)


@router.post("/projects/{project_id}/adopt-draft")
def adopt_draft(project_id: str, body: DraftIn):
    """采用助手草案（只写库，**不重复调用 LLM**）。"""
    return projects.adopt_draft(project_id, body.kind, body.content, body.base_version)


# ---------------- S3：镜头与分镜 ----------------
class ShotsIn(BaseModel):
    script_id: str
    shots: list[dict]
    model_id: str | None = None


class ShotPatch(BaseModel):
    description: str | None = None
    duration_s: int | None = None
    motion: str | None = None
    expected_revision: int | None = None


class ReorderIn(BaseModel):
    order: list[str]


class StoryboardQuoteIn(BaseModel):
    shot_ids: list[str]
    model_id: str
    size: str = "2048x2048"
    prompt: str | None = None


class StoryboardGenIn(BaseModel):
    model_id: str
    confirm: bool = False
    size: str = "2048x2048"
    prompt: str | None = None
    input_asset_id: str | None = None


class ReviewIn(BaseModel):
    approve: bool
    reason: str | None = None


@router.post("/projects/{project_id}/shots")
def add_shots(project_id: str, body: ShotsIn):
    return shots.add_shots(project_id, body.script_id, body.shots, body.model_id)


@router.get("/projects/{project_id}/shots")
def list_shots(project_id: str):
    return {"items": shots.list_shots(project_id)}


@router.post("/projects/{project_id}/shots/reorder")
def reorder_shots(project_id: str, body: ReorderIn):
    return shots.reorder_shots(project_id, body.order)


@router.get("/shots/{shot_id}")
def get_shot(shot_id: str):
    return shots.get_shot(shot_id)


@router.patch("/shots/{shot_id}")
def patch_shot(shot_id: str, body: ShotPatch):
    return shots.update_shot(shot_id, description=body.description,
                             duration_s=body.duration_s, motion=body.motion,
                             expected_revision=body.expected_revision)


@router.delete("/shots/{shot_id}")
def delete_shot(shot_id: str):
    return shots.delete_shot(shot_id)


@router.post("/storyboards/quote")
def quote_storyboard(body: StoryboardQuoteIn):
    """分镜图报价（不建任务）。"""
    return shots.quote_storyboard(body.shot_ids, body.model_id, body.size, body.prompt)


@router.post("/shots/{shot_id}/storyboard")
def generate_storyboard(shot_id: str, body: StoryboardGenIn):
    """生成分镜图；confirm=false 时只返回报价。"""
    return shots.generate_storyboard(shot_id, body.model_id, body.confirm, body.size,
                                     body.prompt, body.input_asset_id)


@router.get("/storyboards/{storyboard_id}")
def get_storyboard(storyboard_id: str):
    return shots.get_storyboard(storyboard_id)


@router.post("/storyboards/{storyboard_id}/review")
def review_storyboard(storyboard_id: str, body: ReviewIn):
    return shots.review_storyboard(storyboard_id, body.approve, body.reason)


# ---------------- S4：整组范围（两种平级模式） ----------------
class ScopeIn(BaseModel):
    mode: str                      # full | clips
    shot_ids: list[str]
    specs: dict


class ScopeConfirmIn(BaseModel):
    confirm: bool = True


@router.post("/projects/{project_id}/scope")
def create_scope(project_id: str, body: ScopeIn):
    """整组报价：两种模式共用；不建任务、不调模型。"""
    return scope.create_scope(project_id, body.mode, body.shot_ids, body.specs)


@router.post("/scopes/{scope_id}/confirm")
def confirm_scope(scope_id: str, body: ScopeConfirmIn | None = None):
    return scope.confirm_scope(scope_id, bool((body or ScopeConfirmIn()).confirm))


@router.post("/projects/{project_id}/generate")
def generate(project_id: str, body: dict):
    """按已确认的 scope 提交整组任务（单事务、幂等、单 worker 顺序执行）。"""
    scope_id = (body or {}).get("scope_id")
    if not scope_id:
        raise ValueError("scope_id 必填")
    row = scope._get_scope(scope_id)
    if row["project_id"] != project_id:
        from ..errors import err as _err
        raise _err("VALIDATION_FAILED", "scope 不属于该项目")
    return scope.submit_scope(scope_id)


@router.get("/projects/{project_id}/status")
def project_status(project_id: str):
    """刷新恢复唯一入口：逐镜状态/take/成片。不调用模型。"""
    return scope.project_status(project_id)


@router.post("/takes/{take_id}/retry")
def retry_take(take_id: str):
    """只重试用户选定的失败镜头；成功的镜头不重做。"""
    return scope.retry_take(take_id)
