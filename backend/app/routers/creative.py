"""创作空间路由（S2：项目 / brief / 脚本）。

只做 S2 范围；shots / scope / films / structure-templates 尚未实现（S3 起）。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import director, projects

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
