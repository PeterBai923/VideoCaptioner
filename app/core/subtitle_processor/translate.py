import hashlib
from string import Template
from typing import Callable, Dict, Optional, List, Any, Union
import logging
from pathlib import Path
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from abc import ABC, abstractmethod
from enum import Enum
from openai import OpenAI
import json
from dataclasses import dataclass
import re

from app.core.bk_asr.asr_data import ASRData, ASRDataSeg
from app.core.utils import json_repair
from app.core.subtitle_processor.prompt import TRANSLATE_PROMPT, SINGLE_TRANSLATE_PROMPT
from app.core.storage.cache_manager import CacheManager
from app.config import CACHE_PATH
from app.core.utils.logger import setup_logger
from app.core.utils.openai_client_wrapper import with_openai_retry


logger = setup_logger("subtitle_translator")


# 语言代码映射：名称 -> (英文名称, ISO代码)
LANGUAGE_CODE_MAP = {
    "简体中文": ("Chinese", "zh-Hans"),
    "繁体中文": ("Chinese Traditional", "zh-Hant"),
    "英语": ("English", "en"),
    "日本語": ("Japanese", "ja"),
    "韩语": ("Korean", "ko"),
    "粤语": ("Cantonese", "yue"),
    "法语": ("French", "fr"),
    "德语": ("German", "de"),
    "西班牙语": ("Spanish", "es"),
    "俄语": ("Russian", "ru"),
    "葡萄牙语": ("Portuguese", "pt"),
    "土耳其语": ("Turkish", "tr"),
    "Chinese": ("Chinese", "zh-Hans"),
    "English": ("English", "en"),
    "Japanese": ("Japanese", "ja"),
    "Korean": ("Korean", "ko"),
}


class TranslatorType(Enum):
    """翻译器类型"""

    OPENAI = "openai"


class BaseTranslator(ABC):
    """翻译器基类"""

    def __init__(
        self,
        thread_num: int = 10,
        batch_num: int = 20,
        target_language: str = "Chinese",
        source_language: str = "English",
        retry_times: int = 1,
        timeout: int = 60,
        update_callback: Optional[Callable] = None,
        custom_prompt: Optional[str] = None,
    ):
        self.thread_num = thread_num
        self.batch_num = batch_num
        self.target_language = target_language
        self.source_language = source_language
        self.retry_times = retry_times
        self.timeout = timeout
        self.is_running = True
        self.update_callback = update_callback
        self.custom_prompt = custom_prompt
        self._init_thread_pool()
        self.cache_manager = CacheManager(CACHE_PATH)

    def _init_thread_pool(self):
        """初始化线程池"""
        self.executor = ThreadPoolExecutor(max_workers=self.thread_num)
        import atexit

        atexit.register(self.stop)

    def translate_subtitle(self, subtitle_data: Union[str, ASRData]) -> ASRData:
        """翻译字幕文件"""
        try:
            # 读取字幕文件
            if isinstance(subtitle_data, str):
                asr_data = ASRData.from_subtitle_file(subtitle_data)
            else:
                asr_data = subtitle_data

            # 将ASRData转换为字典格式
            subtitle_dict = {
                str(i): seg.text for i, seg in enumerate(asr_data.segments, 1)
            }

            # 分批处理字幕
            chunks = self._split_chunks(subtitle_dict)

            # 多线程翻译
            translated_dict = self._parallel_translate(chunks)

            # 创建新的ASRDataSeg列表
            new_segments = self._create_segments(asr_data.segments, translated_dict)

            return ASRData(new_segments)
        except Exception as e:
            logger.error(f"翻译失败：{str(e)}")
            raise RuntimeError(f"翻译失败：{str(e)}")

    def _split_chunks(self, subtitle_dict: Dict[str, str]) -> List[Dict[str, str]]:
        """将字幕分割成块"""
        items = list(subtitle_dict.items())
        return [
            dict(items[i : i + self.batch_num])
            for i in range(0, len(items), self.batch_num)
        ]

    def _parallel_translate(self, chunks: List[Dict[str, str]]) -> Dict[str, str]:
        """并行翻译所有块"""
        futures = []
        translated_dict = {}

        for chunk in chunks:
            future = self.executor.submit(self._safe_translate_chunk, chunk)
            futures.append(future)

        for future in as_completed(futures):
            if not self.is_running:
                logger.info("翻译器已停止运行，退出翻译")
                break
            try:
                result = future.result()
                translated_dict.update(result)
            except Exception as e:
                logger.error(f"翻译块失败：{str(e)}")
                # 对于失败的块，保留原文
                for k, v in chunk.items():
                    translated_dict[k] = f"{v}||ERROR"

        return translated_dict

    def _safe_translate_chunk(self, chunk: Dict[str, str]) -> Dict[str, str]:
        """安全的翻译块，包含重试逻辑"""
        for i in range(self.retry_times):
            try:
                result = self._translate_chunk(chunk)
                if self.update_callback:
                    self.update_callback(result)
                return result
            except Exception as e:
                if i == self.retry_times - 1:
                    raise
                logger.warning(f"翻译重试 {i+1}/{self.retry_times}: {str(e)}")

    @staticmethod
    def _create_segments(
        original_segments: List[ASRDataSeg], translated_dict: Dict[str, str]
    ) -> List[ASRDataSeg]:
        """创建新的字幕段"""
        for i, seg in enumerate(original_segments, 1):
            try:
                seg.translated_text = translated_dict[str(i)]  # 设置翻译文本
            except Exception as e:
                logger.error(f"创建新的字幕段失败：{str(e)}")
                seg.translated_text = seg.text
        return original_segments

    @abstractmethod
    def _translate_chunk(self, subtitle_chunk: Dict[str, str]) -> Dict[str, str]:
        """翻译字幕块"""
        pass

    def stop(self):
        """停止翻译器"""
        if not self.is_running:
            return

        logger.info("正在停止翻译器...")
        self.is_running = False
        if hasattr(self, "executor") and self.executor is not None:
            try:
                self.executor.shutdown(wait=False, cancel_futures=True)
            except Exception as e:
                logger.error(f"关闭线程池时出错：{str(e)}")
            finally:
                self.executor = None


class OpenAITranslator(BaseTranslator):
    """OpenAI翻译器"""

    def __init__(
        self,
        thread_num: int = 10,
        batch_num: int = 20,
        target_language: str = "Chinese",
        source_language: str = "English",
        model: str = "gpt-4o-mini",
        custom_prompt: str = "",
        temperature: float = 0.7,
        timeout: int = 60,
        retry_times: int = 1,
        update_callback: Optional[Callable] = None,
    ):
        super().__init__(
            thread_num=thread_num,
            batch_num=batch_num,
            target_language=target_language,
            source_language=source_language,
            retry_times=retry_times,
            timeout=timeout,
            update_callback=update_callback,
        )

        self._init_client()
        self.model = model
        self.custom_prompt = custom_prompt
        self.temperature = temperature

    def _get_language_info(self, lang: str) -> tuple:
        """获取语言信息（英文名称和ISO代码）"""
        if lang in LANGUAGE_CODE_MAP:
            return LANGUAGE_CODE_MAP[lang]
        # 默认返回原名称和相同代码
        return (lang, lang.lower()[:2])

    def _init_client(self):
        """初始化OpenAI客户端"""
        base_url = os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("OPENAI_API_KEY")
        if not (base_url and api_key):
            raise ValueError("环境变量 OPENAI_BASE_URL 和 OPENAI_API_KEY 必须设置")

        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def _translate_chunk(self, subtitle_chunk: Dict[str, str]) -> Dict[str, str]:
        """翻译字幕块"""
        return self._translate_chunk_standard(subtitle_chunk)

    def _translate_chunk_standard(self, subtitle_chunk: Dict[str, str]) -> Dict[str, str]:
        """标准非流式翻译字幕块"""
        logger.info(
            f"[+]正在翻译字幕：{next(iter(subtitle_chunk))} - {next(reversed(subtitle_chunk))}"
        )

        source_lang, source_code = self._get_language_info(self.source_language)
        target_lang, target_code = self._get_language_info(self.target_language)
        # TranslateGemma 格式：单个用户消息，提示词 + 两个空行 + 文本
        text_to_translate = "\n".join(subtitle_chunk.values())
        user_content = Template(TRANSLATE_PROMPT).safe_substitute(
            source_lang=source_lang,
            source_code=source_code,
            target_lang=target_lang,
            target_code=target_code,
        ) + text_to_translate
        system_prompt = None

        prompt_hash = hashlib.md5((system_prompt or user_content).encode()).hexdigest()

        try:
            # 检查缓存
            cache_params = {
                "target_language": self.target_language,
                "source_language": self.source_language,
                "temperature": self.temperature,
                "prompt_hash": prompt_hash,
            }
            cache_key = f"{json.dumps(subtitle_chunk, ensure_ascii=False)}"
            cache_result = self.cache_manager.get_llm_result(
                cache_key,
                self.model,
                **cache_params,
            )

            result = {}
            if cache_result:
                result = json.loads(cache_result)
            else:
                # 调用API翻译
                response = self._call_api(system_prompt, user_content)
                # 按换行分割响应文本
                lines = response.choices[0].message.content.strip().split("\n")
                # 重建字典，保持与输入的对应关系
                keys = list(subtitle_chunk.keys())
                if len(lines) != len(keys):
                    logger.warning(f"翻译结果数量不匹配({len(lines)} vs {len(keys)})，将使用单条翻译模式重试")
                    return self._translate_chunk_single(subtitle_chunk)
                result = {keys[i]: lines[i] for i in range(len(keys))}
                # 保存到缓存
                self.cache_manager.set_llm_result(
                    cache_key,
                    json.dumps(result, ensure_ascii=False),
                    self.model,
                    **cache_params,
                )

            result = {k: f"{v}" for k, v in result.items()}

            return result
        except Exception as e:
            try:
                return self._translate_chunk_single(subtitle_chunk)
            except Exception as e:
                logger.error(f"翻译失败：{str(e)}")
                raise RuntimeError(f"OpenAI API调用失败：{str(e)}")

    def _translate_chunk_single(self, subtitle_chunk: Dict[str, str]) -> Dict[str, str]:
        """单条翻译模式"""
        result = {}
        single_prompt = Template(SINGLE_TRANSLATE_PROMPT).safe_substitute(
            target_language=self.target_language
        )
        prompt_hash = hashlib.md5(single_prompt.encode()).hexdigest()
        for idx, text in subtitle_chunk.items():
            try:
                # 检查缓存
                cache_params = {
                    "target_language": self.target_language,
                    "temperature": self.temperature,
                    "prompt_hash": prompt_hash,
                }
                cache_result = self.cache_manager.get_llm_result(
                    f"{text}", self.model, **cache_params
                )

                if cache_result:
                    result[idx] = cache_result
                    continue

                response = self._call_api(single_prompt, text)
                translated_text = response.choices[0].message.content.strip()

                # 删除 DeepSeek-R1 等推理模型的思考过程 #300
                translated_text = re.sub(
                    r"<!think>.*?<!/think>", "", translated_text, flags=re.DOTALL
                )
                translated_text = translated_text.strip()

                # 保存到缓存
                self.cache_manager.set_llm_result(
                    f"{text}",
                    translated_text,
                    self.model,
                    **cache_params,
                )

                result[idx] = translated_text
            except Exception as e:
                logger.error(f"单条翻译失败 {idx}: {str(e)}")
                result[idx] = "ERROR"  # 如果翻译失败，返回错误标记

        return result

    @with_openai_retry(max_retries=20, delay_increment=0.5)
    def _call_api(self, system_prompt: Optional[str], user_content: str) -> Any:
        """调用OpenAI API"""
        if system_prompt:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
        else:
            # TranslateGemma 格式：只有用户消息
            messages = [{"role": "user", "content": user_content}]

        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            timeout=self.timeout,
        )


class TranslatorFactory:
    """翻译器工厂类"""

    @staticmethod
    def create_translator(
        translator_type: TranslatorType,
        thread_num: int = 5,
        batch_num: int = 10,
        target_language: str = "Chinese",
        source_language: str = "English",
        model: str = "gpt-4o-mini",
        custom_prompt: str = "",
        temperature: float = 0.7,
        update_callback: Optional[Callable] = None,
    ) -> BaseTranslator:
        """创建翻译器实例"""
        try:
            if translator_type == TranslatorType.OPENAI:
                return OpenAITranslator(
                    thread_num=thread_num,
                    batch_num=batch_num,
                    target_language=target_language,
                    source_language=source_language,
                    model=model,
                    custom_prompt=custom_prompt,
                    temperature=temperature,
                    update_callback=update_callback,
                )
            else:
                raise ValueError(f"不支持的翻译器类型：{translator_type}")
        except Exception as e:
            logger.error(f"创建翻译器失败：{str(e)}")
            raise
