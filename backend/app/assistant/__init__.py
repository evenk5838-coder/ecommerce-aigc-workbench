"""可选创作助手（阶段一：接口骨架 + 报价/确认门禁 + 限制与用量）。

本包不自行实现任务系统、计价、素材或模板：
- 任务：复用 `app.main.resolve_task` / `POST /api/tasks`
- 计价：复用 `app.cloud_tasks.estimate_cost`
- 模板/素材：复用 `app.templates` / `assets` 表

**未接入真实 LLM**：`ASSISTANT_PROVIDER` 默认 `none`，接口返回"暂不可用"。
"""
from __future__ import annotations

from . import approval, provider, router, service, tools

__all__ = ["approval", "provider", "router", "service", "tools"]
