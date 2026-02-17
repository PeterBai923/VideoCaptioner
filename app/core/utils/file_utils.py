"""文件扫描工具模块"""

from pathlib import Path
from typing import List, Set, Callable, Optional

from app.core.entities import (
    BatchTaskType,
    SupportedAudioFormats,
    SupportedVideoFormats,
    SupportedSubtitleFormats,
)


def get_supported_extensions(task_type: BatchTaskType) -> Set[str]:
    """
    根据任务类型获取支持的文件扩展名

    Args:
        task_type: 批量任务类型

    Returns:
        支持的文件扩展名集合（包含点前缀，如 ".mp4"）
    """
    if task_type in [
        BatchTaskType.TRANSCRIBE,
        BatchTaskType.TRANS_SUB,
        BatchTaskType.FULL_PROCESS,
    ]:
        audio_exts = {f".{fmt.value}" for fmt in SupportedAudioFormats}
        video_exts = {f".{fmt.value}" for fmt in SupportedVideoFormats}
        return audio_exts | video_exts
    elif task_type == BatchTaskType.SUBTITLE:
        return {f".{fmt.value}" for fmt in SupportedSubtitleFormats}
    return set()


def scan_folder_recursively(
    folder_path: str,
    extensions: Set[str],
    is_interrupted: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[int], None]] = None
) -> List[str]:
    """
    递归扫描文件夹，返回匹配扩展名的文件

    Args:
        folder_path: 要扫描的文件夹路径
        extensions: 允许的文件扩展名集合（包含点前缀，如 ".mp4"）
        is_interrupted: 可选的中断检查回调函数，返回 True 表示应中断扫描
        progress_callback: 可选的进度回调函数，参数为已扫描的文件数

    Returns:
        匹配的文件路径列表，按文件名排序
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        return []

    matched_files = []
    extensions_lower = {ext.lower() for ext in extensions}
    scan_count = 0

    try:
        for file_path in folder.rglob("*"):
            # 检查是否被中断
            if is_interrupted and is_interrupted():
                return matched_files
            scan_count += 1
            # 每 10 个文件报告一次进度
            if progress_callback and scan_count % 10 == 0:
                progress_callback(scan_count)
            try:
                if file_path.is_file() and file_path.suffix.lower() in extensions_lower:
                    matched_files.append(str(file_path))
            except (PermissionError, OSError):
                continue  # 跳过无法访问的文件
    except (PermissionError, OSError):
        pass  # 无法访问目录时返回已找到的文件

    # 按文件名排序
    matched_files.sort(key=lambda x: Path(x).name.lower())
    return matched_files
