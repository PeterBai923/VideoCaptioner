import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

from ..utils.logger import setup_logger

logger = setup_logger("video_utils")


def video2audio(input_file: str, output: str = "") -> bool:
    """使用ffmpeg将视频转换为音频"""
    # 创建output目录
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output = str(output)
    cmd = [
        "ffmpeg",
        "-i",
        input_file,
        "-map",
        "0:a",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        "aresample=async=1",  # 处理音频同步问题
        "-y",
        output,
    ]
    logger.info(f"转换为音频执行命令: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        if result.returncode == 0 and Path(output).is_file():
            return True
        else:
            logger.error("音频转换失败")
            return False
    except Exception as e:
        logger.exception(f"音频转换出错: {str(e)}")
        return False


def get_video_info(file_path: str) -> Optional[Dict]:
    """获取视频信息"""
    try:
        cmd = ["ffmpeg", "-i", file_path]

        # logger.info(f"获取视频信息执行命令: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        info = result.stderr

        video_info_dict = {
            "file_name": Path(file_path).stem,
            "file_path": file_path,
            "duration_seconds": 0,
            "bitrate_kbps": 0,
            "video_codec": "",
            "width": 0,
            "height": 0,
            "fps": 0,
            "audio_codec": "",
            "audio_sampling_rate": 0,
            "thumbnail_path": "",
        }

        # 提取时长
        if duration_match := re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", info):
            hours, minutes, seconds = map(float, duration_match.groups())
            video_info_dict["duration_seconds"] = hours * 3600 + minutes * 60 + seconds

        # 提取比特率
        if bitrate_match := re.search(r"bitrate: (\d+) kb/s", info):
            video_info_dict["bitrate_kbps"] = int(bitrate_match.group(1))

        # 提取视频流信息
        if video_stream_match := re.search(
            r"Stream #.*?Video: (\w+)(?:\s*\([^)]*\))?.* (\d+)x(\d+).*?(?:(\d+(?:\.\d+)?)\s*(?:fps|tb[rn]))",
            info,
            re.DOTALL,
        ):
            video_info_dict.update(
                {
                    "video_codec": video_stream_match.group(1),
                    "width": int(video_stream_match.group(2)),
                    "height": int(video_stream_match.group(3)),
                    "fps": float(video_stream_match.group(4)),
                }
            )
        else:
            logger.warning("未找到视频流信息")

        return video_info_dict
    except Exception as e:
        logger.exception(f"获取视频信息时出错: {str(e)}")
        return None
