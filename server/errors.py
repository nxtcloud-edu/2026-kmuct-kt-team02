"""Common HTTP and streaming error contracts."""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field

from server.schemas import ContractModel

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    SESSION_EXPIRED = "session_expired"
    POLICY_NOT_FOUND = "policy_not_found"
    SERVER_ERROR = "server_error"
    ANSWER_FAILED = "answer_failed"


_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_INPUT: "입력한 정보를 다시 확인해 주세요",
    ErrorCode.SESSION_EXPIRED: "시간이 지나 처음부터 다시 시작할게요",
    ErrorCode.POLICY_NOT_FOUND: "정책을 찾을 수 없어요",
    ErrorCode.SERVER_ERROR: "잠시 문제가 생겼어요. 다시 시도해 주세요",
    ErrorCode.ANSWER_FAILED: "설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요",
}


class ValidationIssue(ContractModel):
    location: list[str | int]
    message: str
    error_type: str


class ErrorPayload(ContractModel):
    code: ErrorCode
    message: str
    details: list[ValidationIssue] = Field(default_factory=list)


class ErrorResponse(ContractModel):
    error: ErrorPayload


class AppError(Exception):
    """Expected application error safe to return to an API client."""

    def __init__(
        self,
        *,
        code: ErrorCode,
        status_code: int,
        message: str | None = None,
    ) -> None:
        super().__init__(message or _DEFAULT_MESSAGES[code])
        self.code = code
        self.status_code = status_code
        self.message = message or _DEFAULT_MESSAGES[code]


def _response(status_code: int, payload: ErrorPayload) -> JSONResponse:
    body = ErrorResponse(error=payload)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return _response(
        exc.status_code,
        ErrorPayload(code=exc.code, message=exc.message),
    )


async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    issues: list[ValidationIssue] = []
    for error in exc.errors():
        location = [
            part if isinstance(part, (str, int)) else str(part)
            for part in error.get("loc", ())
        ]
        issues.append(
            ValidationIssue(
                location=location,
                message=str(error.get("msg", "invalid value")),
                error_type=str(error.get("type", "value_error")),
            )
        )

    return _response(
        422,
        ErrorPayload(
            code=ErrorCode.INVALID_INPUT,
            message=_DEFAULT_MESSAGES[ErrorCode.INVALID_INPUT],
            details=issues,
        ),
    )


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server error", exc_info=exc)
    return _response(
        500,
        ErrorPayload(
            code=ErrorCode.SERVER_ERROR,
            message=_DEFAULT_MESSAGES[ErrorCode.SERVER_ERROR],
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Install the same envelope for expected, validation, and server errors."""

    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
