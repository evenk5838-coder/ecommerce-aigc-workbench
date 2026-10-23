"""统一错误结构与错误码（与架构方案 §12 对齐）。"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, code: str, message: str, http: int = 400, detail: dict | None = None, task_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http
        self.detail = detail or {}
        self.task_id = task_id

    def to_dict(self) -> dict:
        err: dict = {"code": self.code, "message": self.message}
        if self.task_id:
            err["task_id"] = self.task_id
        if self.detail:
            err["detail"] = self.detail
        return {"error": err}


CODES = {
    "VALIDATION_FAILED": (400, False),
    "ASSET_NOT_FOUND": (404, False),
    "TASK_NOT_FOUND": (404, False),
    "DRAFT_NOT_FOUND": (404, False),
    "DRAFT_VERSION_CONFLICT": (409, False),
    "MODEL_NOT_DOWNLOADED": (409, False),
    "MODEL_LOAD_FAILED": (500, False),
    "MODEL_NUMERICAL_ERROR": (500, False),
    "REFERENCE_IMAGE_REQUIRED": (400, False),
    "ASSET_NOT_IMAGE": (400, False),
    "INVALID_REFERENCE_IMAGE": (400, False),
    "MODEL_UNSUPPORTED_OP": (500, False),
    "MODEL_OOM": (507, False),
    "MEMORY_PREDICTED_OVER": (507, False),
    "CANCEL_REJECTED_PHASE": (409, False),
    "MEDIA_ENCODE_FAILED": (502, True),
    "TASK_INTERRUPTED": (409, True),
    "UPLOAD_REJECTED": (415, False),
    "INTERNAL_ERROR": (500, False),
    "AUTH_MISSING": (400, False),
    "AUTH_FAILED": (502, False),
    "PROVIDER_BAD_REQUEST": (400, False),
    "PROVIDER_RATE_LIMITED": (429, True),
    "PROVIDER_UNAVAILABLE": (502, True),
    "CONTENT_REJECTED": (422, False),
    "CREATE_RESULT_UNKNOWN": (202, False),
    "DOWNLOAD_FAILED": (502, True),
    "BUDGET_NOT_APPROVED": (402, False),
    "BUDGET_EXCEEDED": (402, False),
    "MODEL_NOT_OPENED": (502, False),
    "RESULT_URL_EXPIRED": (409, False),
    # v0.7 模板相关
    "TEMPLATE_NOT_FOUND": (404, False),
    "TEMPLATE_VERSION_NOT_FOUND": (404, False),
    "TEMPLATE_FIELD_REQUIRED": (400, False),
    "TEMPLATE_FIELD_INVALID": (400, False),
    "TEMPLATE_DEPRECATED": (409, False),
    "MODEL_TEMPLATE_INCOMPATIBLE": (400, False),
    "MODEL_EOS": (409, False),
}


def err(code: str, message: str, detail: dict | None = None, task_id: str | None = None) -> AppError:
    http, _ = CODES.get(code, (500, False))
    return AppError(code, message, http=http, detail=detail, task_id=task_id)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.http, content=exc.to_dict())


async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    # 不泄漏堆栈
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "服务内部错误，请查看后端日志"}},
    )
