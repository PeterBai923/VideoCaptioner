"""
OpenAI API 客户端包装器

提供智能重试装饰器，支持线性退避策略。
"""

import functools
import logging
import time
import traceback
from typing import Any, Callable, Optional

from .openai_error_handler import (
    OpenAIErrorCategory,
    OpenAIErrorInfo,
    classify_openai_error,
    get_error_type_name,
)
from .logger import setup_logger

logger = setup_logger("openai_retry_wrapper")


def with_openai_retry(
    max_retries: int = 20,
    delay_increment: float = 0.5,
) -> Callable:
    """
    OpenAI API 调用重试装饰器

    重试策略：
    - 线性退避：每次重试等待时间增加 delay_increment 秒
    - 第1次重试等待 delay_increment 秒
    - 第2次重试等待 2 * delay_increment 秒
    - 第N次重试等待 N * delay_increment 秒

    可重试错误：
    - 429 Too Many Requests (速率限制)
    - 500 Internal Server Error
    - 502 Bad Gateway
    - 503 Service Unavailable
    - 504 Gateway Timeout
    - 连接超时
    - 读取超时

    不可重试错误（立即抛出）：
    - 400 Bad Request
    - 401 Unauthorized
    - 403 Forbidden
    - 404 Not Found

    Args:
        max_retries: 最大重试次数，默认 20
        delay_increment: 每次重试增加的等待时间（秒），默认 0.5

    Returns:
        装饰器函数

    Example:
        @with_openai_retry(max_retries=20, delay_increment=0.5)
        def call_openai_api():
            return client.chat.completions.create(...)
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            last_error: Optional[Exception] = None
            func_name = func.__name__
            module_name = func.__module__

            # 获取调用上下文
            caller_info = _get_caller_info()

            for attempt in range(1, max_retries + 1):
                try:
                    result = func(*args, **kwargs)

                    # 如果成功且有重试历史，记录成功信息
                    if attempt > 1:
                        total_time = time.time() - start_time
                        logger.info(
                            f"API调用成功 ({caller_info}) - 共尝试 {attempt} 次，"
                            f"总耗时: {total_time:.1f} 秒"
                        )

                    return result

                except Exception as e:
                    last_error = e
                    error_info = classify_openai_error(e)

                    # 检查是否为不可重试错误
                    if not error_info.is_retryable():
                        logger.error(
                            f"API调用失败 ({caller_info}) - 不可重试错误: "
                            f"{get_error_type_name(error_info)} "
                            f"({error_info.status_code}) - {error_info.message}"
                        )
                        raise

                    # 计算等待时间
                    # 优先使用服务器返回的 Retry-After 值
                    # 否则使用线性退避：attempt * delay_increment
                    if error_info.retry_after is not None:
                        wait_time = error_info.retry_after
                    else:
                        wait_time = attempt * delay_increment

                    # 如果这是最后一次尝试，不再等待
                    if attempt >= max_retries:
                        break

                    # 记录重试信息
                    logger.warning(
                        f"API调用失败 ({caller_info}) - 尝试 {attempt}/{max_retries}"
                    )
                    logger.warning(
                        f"错误类型: {get_error_type_name(error_info)} "
                        f"({error_info.status_code})"
                    )
                    logger.warning(f"错误信息: {error_info.message}")
                    logger.warning(f"等待 {wait_time:.1f} 秒后重试...")

                    # 等待后重试
                    time.sleep(wait_time)

            # 所有重试都失败，记录最终错误
            total_time = time.time() - start_time
            error_info = classify_openai_error(last_error)

            logger.error(f"所有重试均失败 ({caller_info})")
            logger.error(f"共尝试 {max_retries} 次，总耗时: {total_time:.1f} 秒")
            logger.error(
                f"最后错误: {get_error_type_name(error_info)} "
                f"({error_info.status_code}) - {error_info.message}"
            )

            raise last_error

        return wrapper

    return decorator


def _get_caller_info() -> str:
    """
    获取调用者的文件名和函数名

    Returns:
        str: 格式为 "filename.py:function_name" 的字符串
    """
    try:
        # 获取调用栈
        stack = traceback.extract_stack()
        # 找到调用 with_openai_retry 装饰器的位置
        # skip: wrapper, decorator func, the actual decorated func, _get_caller_info
        for frame in reversed(stack[:-4]):
            filename = frame.filename
            # 跳过本模块的调用
            if "openai_client_wrapper.py" not in filename:
                # 只显示文件名，不显示完整路径
                import os
                filename = os.path.basename(filename)
                func_name = frame.name
                return f"{filename}:{func_name}"
    except Exception:
        pass
    return "unknown:unknown"
