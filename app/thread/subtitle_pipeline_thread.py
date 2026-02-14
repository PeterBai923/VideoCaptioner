import datetime
from typing import Any

from PyQt5.QtCore import QThread, pyqtSignal

from app.core.utils.logger import setup_logger

from .subtitle_thread import SubtitleThread
from .transcript_thread import TranscriptThread

logger = setup_logger("subtitle_pipeline_thread")

class SubtitlePipelineThread(QThread):
    """字幕处理全流程线程，包含:
    1. 转录生成字幕
    2. 字幕优化/翻译
    """
    progress = pyqtSignal(int, str)  # 进度值, 进度描述
    finished = pyqtSignal(object)  # 任务对象
    error = pyqtSignal(str)

    def __init__(self, task: Any):
        super().__init__()
        self.task = task
        self.has_error = False

    def run(self):
        try:
            def handle_error(error_msg):
                logger.error("pipeline 发生错误: %s", error_msg)
                self.has_error = True
                self.error.emit(error_msg)

            # 1. 转录生成字幕
            logger.info(f"\n===========任务开始===========")
            logger.info(f"时间：{datetime.datetime.now()}")
            logger.info("开始转录")
            self.progress.emit(0, self.tr("开始转录"))
            transcript_thread = TranscriptThread(self.task)
            transcript_thread.progress.connect(lambda value, msg: self.progress.emit(int(value * 0.5), msg))
            transcript_thread.error.connect(handle_error)
            transcript_thread.run()

            if self.has_error:
                logger.info("转录过程中发生错误，终止流程")
                return

            # 2. 字幕优化/翻译
            self.progress.emit(50, self.tr("开始优化字幕"))
            optimization_thread = SubtitleThread(self.task)
            optimization_thread.progress.connect(lambda value, msg: self.progress.emit(int(50 + value * 0.5), msg))
            optimization_thread.error.connect(handle_error)
            optimization_thread.run()

            if self.has_error:
                logger.info("字幕优化过程中发生错误，终止流程")
                return

            logger.info("处理完成")
            self.progress.emit(100, self.tr("处理完成"))
            self.finished.emit(self.task)

        except Exception as e:
            logger.exception("处理失败: %s", str(e))
            self.error.emit(str(e))
