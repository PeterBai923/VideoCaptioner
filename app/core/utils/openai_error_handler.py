"""
OpenAI API 错误处理模块

提供错误分类、Retry-After 头部提取和自定义异常类。
"""

import logging
from enum import Enum
from typing import Optional, Union

from openai import OpenAIError


logger = logging.getLogger(__name__)


class OpenAIErrorCategory(Enum):
    """OpenAI API 错误分类"""

    # 可重试错误（服务器端或临时问题）
    RETRYABLE_RATE_LIMIT = "rate_limit"  # 429 Too Many Requests
    RETRYABLE_SERVER_ERROR = "server_error"  # 500, 502, 503, 504
    RETRYABLE_TIMEOUT = "timeout"  # 连接或读取超时

    # 不可重试错误（客户端错误）
    NON_RETRYABLE_BAD_REQUEST = "bad_request"  # 400
    NON_RETRYABLE_UNAUTHORIZED = "unauthorized"  # 401
    NON_RETRYABLE_FORBIDDEN = "forbidden"  # 403
    NON_RETRYABLE_NOT_FOUND = "not_found"  # 404

    # 其他错误
    UNKNOWN = "unknown"


class OpenAIErrorInfo:
    """OpenAI API 错误信息"""

    def __init__(
        self,
        category: OpenAIErrorCategory,
        status_code: Optional[int] = None,
        message: str = "",
        retry_after: Optional[float] = None,
    ):
        self.category = category
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after

    def is_retryable(self) -> bool:
        """判断错误是否可重试"""
        return self.category in {
            OpenAIErrorCategory.RETRYABLE_RATE_LIMIT,
            OpenAIErrorCategory.RETRYABLE_SERVER_ERROR,
            OpenAIErrorCategory.RETRYABLE_TIMEOUT,
        }

    def __repr__(self) -> str:
        return (
            f"OpenAIErrorInfo(category={self.category.value}, "
            f"status_code={self.status_code}, message='{self.message}', "
            f"retry_after={self.retry_after})"
        )


def classify_openai_error(error: Exception) -> OpenAIErrorInfo:
    """
    对 OpenAI API 错误进行分类

    Args:
        error: 捕获的异常对象

    Returns:
        OpenAIErrorInfo: 错误信息对象
    """
    # 默认错误信息
    error_info = OpenAIErrorInfo(
        category=OpenAIErrorCategory.UNKNOWN,
        status_code=None,
        message=str(error),
        retry_after=None,
    )

    # 处理 OpenAI 特定错误
    if isinstance(error, OpenAIError):
        # 尝试获取状态码和错误信息
        status_code = None
        error_message = str(error)
        retry_after = None

        # 从错误中提取状态码
        if hasattr(error, "status_code"):
            status_code = error.status_code
        elif hasattr(error, "response"):
            response = getattr(error, "response", None)
            if response is not None and hasattr(response, "status_code"):
                status_code = response.status_code
                # 尝试提取 Retry-After 头部
                retry_after = _extract_retry_after(response)

        # 从错误中提取错误消息
        if hasattr(error, "message"):
            error_message = error.message
        elif hasattr(error, "body"):
            body = getattr(error, "body", {})
            if isinstance(body, dict) and "message" in body:
                error_message = body["message"]

        # 根据状态码分类
        if status_code:
            if status_code == 429:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.RETRYABLE_RATE_LIMIT,
                    status_code=status_code,
                    message=error_message,
                    retry_after=retry_after,
                )
            elif status_code in {500, 502, 503, 504}:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.RETRYABLE_SERVER_ERROR,
                    status_code=status_code,
                    message=error_message,
                    retry_after=retry_after,
                )
            elif status_code == 400:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.NON_RETRYABLE_BAD_REQUEST,
                    status_code=status_code,
                    message=error_message,
                    retry_after=None,
                )
            elif status_code == 401:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.NON_RETRYABLE_UNAUTHORIZED,
                    status_code=status_code,
                    message=error_message,
                    retry_after=None,
                )
            elif status_code == 403:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.NON_RETRYABLE_FORBIDDEN,
                    status_code=status_code,
                    message=error_message,
                    retry_after=None,
                )
            elif status_code == 404:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.NON_RETRYABLE_NOT_FOUND,
                    status_code=status_code,
                    message=error_message,
                    retry_after=None,
                )
            else:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.UNKNOWN,
                    status_code=status_code,
                    message=error_message,
                    retry_after=None,
                )
        else:
            # 没有状态码，根据错误类型判断
            error_type = type(error).__name__
            error_message_lower = error_message.lower()

            # 速率限制错误
            if "rate" in error_message_lower or "limit" in error_message_lower:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.RETRYABLE_RATE_LIMIT,
                    status_code=None,
                    message=error_message,
                    retry_after=None,
                )
            # 超时错误
            elif (
                "timeout" in error_type.lower() or "timeout" in error_message_lower
            ) or "timed out" in error_message_lower:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.RETRYABLE_TIMEOUT,
                    status_code=None,
                    message=error_message,
                    retry_after=None,
                )
            # 连接错误（通常可重试）
            elif "connection" in error_type.lower():
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.RETRYABLE_SERVER_ERROR,
                    status_code=None,
                    message=error_message,
                    retry_after=None,
                )
            else:
                error_info = OpenAIErrorInfo(
                    category=OpenAIErrorCategory.UNKNOWN,
                    status_code=None,
                    message=error_message,
                    retry_after=None,
                )
    else:
        # 非 OpenAI 错误，尝试判断
        error_type = type(error).__name__
        error_message_lower = str(error).lower()

        # 超时错误
        if (
            "timeout" in error_type.lower() or "timeout" in error_message_lower
        ) or "timed out" in error_message_lower:
            error_info = OpenAIErrorInfo(
                category=OpenAIErrorCategory.RETRYABLE_TIMEOUT,
                status_code=None,
                message=str(error),
                retry_after=None,
            )
        # 连接错误
        elif "connection" in error_type.lower():
            error_info = OpenAIErrorInfo(
                category=OpenAIErrorCategory.RETRYABLE_SERVER_ERROR,
                status_code=None,
                message=str(error),
                retry_after=None,
            )

    return error_info


def _extract_retry_after(response) -> Optional[float]:
    """
    从 HTTP 响应中提取 Retry-After 头部值

    Args:
        response: HTTP 响应对象

    Returns:
        float: 重试等待秒数，如果不存在则返回 None
    """
    try:
        headers = getattr(response, "headers", {})
        if isinstance(headers, dict):
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    # Retry-After 可能是秒数或 HTTP-date
                    return float(retry_after)
                except ValueError:
                    pass
    except Exception:
        pass
    return None


def get_error_type_name(error_info: OpenAIErrorInfo) -> str:
    """
    获取错误类型的可读名称

    Args:
        error_info: 错误信息对象

    Returns:
        str: 错误类型名称
    """
    status_code = error_info.status_code
    if status_code:
        status_descriptions = {
            400: "BadRequest",
            401: "Unauthorized",
            403: "Forbidden",
            404: "NotFound",
            429: "RateLimitError",
            500: "InternalServerError",
            502: "BadGateway",
            503: "ServiceUnavailable",
            504: "GatewayTimeout",
        }
        return status_descriptions.get(status_code, f"HTTP_{status_code}")
    return type(error_info.category).__name__
