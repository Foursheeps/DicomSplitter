#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DICOM Splitter v1.76 - DICOM 序列分割与 NIfTI 转换工具
优化说明：修复潜在 bug，增强健壮性，优化性能

Copyright (c) 2026 Foursheeps
Licensed under MIT License
"""

import os
import re
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import List, Dict, Optional, Set, Callable, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import Thread
import queue

import pydicom
from pydicom.errors import InvalidDicomError
from loguru import logger
from func_timeout import func_timeout, FunctionTimedOut
import SimpleITK as sitk

# -------------------------- 全局配置与常量 --------------------------
DEFAULT_TIMEOUT = 2
DEFAULT_MIN_SLICES = 10
DEFAULT_N_JOBS = 4
LOG_DIR = Path("log")
LOG_FILE = LOG_DIR / "dicom_splitter_app.log"

# SliceLocation 浮点精度容差
SLICE_LOCATION_TOLERANCE = 0.001

# 文件名最大长度
MAX_FILENAME_LENGTH = 200

# 确保日志目录存在
LOG_DIR.mkdir(exist_ok=True, parents=True)

# 配置日志 - 使用单例模式避免重复配置
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO"
)
logger.add(
    LOG_FILE,
    rotation="100 MB",
    retention="30 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level="DEBUG"
)

# -------------------------- 工具函数 --------------------------
def sanitize_file_name(file_name: Optional[str]) -> str:
    """
    清理文件名，移除非法字符并替换为下划线

    Args:
        file_name: 原始文件名

    Returns:
        清理后的文件名
    """
    if not file_name:
        return "unknown"

    # 转换为字符串
    sanitized = str(file_name)

    # 移除或替换非法字符（包括 Windows 和 Unix 的非法字符）
    sanitized = re.sub(r'[\\/*?:"<>|\[\]\x00-\x1f]', "_", sanitized)

    # 移除前后的空白和点
    sanitized = sanitized.strip().strip(".")

    # 替换连续的下划线和空格
    sanitized = re.sub(r"[_\s]+", "_", sanitized)

    # 限制长度
    if len(sanitized) > MAX_FILENAME_LENGTH:
        sanitized = sanitized[:MAX_FILENAME_LENGTH - 3] + "..."

    return sanitized if sanitized else "unknown"


def safe_get(dicom_obj: Any, tag: str, default: Any = "") -> Any:
    """
    安全地获取 DICOM 对象的属性值

    Args:
        dicom_obj: DICOM 对象
        tag: 属性名或标签
        default: 默认值

    Returns:
        属性值或默认值
    """
    try:
        value = getattr(dicom_obj, tag, default)

        # 处理空值和无效值
        if value is None or value == "":
            return default

        # 处理 pydicom 多值类型
        if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
            if len(value) == 0:
                return default
            if len(value) == 1:
                value = value[0]
            else:
                # 多值情况返回第一个值
                value = value[0]

        # 转换为字符串并清理
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default

        return value
    except Exception:
        return default


def is_dicom_file(file_path: Path, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """
    检查文件是否为有效的 DICOM 文件

    Args:
        file_path: 文件路径
        timeout: 超时时间（秒）

    Returns:
        是否为 DICOM 文件
    """
    try:
        def check():
            with open(file_path, "rb") as f:
                # 尝试读取 DICOM 前缀
                f.seek(128)
                prefix = f.read(4)
                if prefix == b"DICM":
                    return True
                # 尝试直接读取（一些 DICOM 文件没有前缀）
                f.seek(0)
                try:
                    pydicom.dcmread(f, stop_before_pixels=True, force=True)
                    return True
                except Exception:
                    return False

        return func_timeout(timeout, check)
    except FunctionTimedOut:
        logger.warning(f"检查文件类型超时: {file_path}")
        return False
    except Exception as e:
        logger.debug(f"检查文件类型失败 {file_path}: {e}")
        return False


def get_dicom_file(root_path: str, timeout: int = DEFAULT_TIMEOUT) -> List[Path]:
    """
    递归获取目录下所有 DICOM 文件，带超时保护

    Args:
        root_path: 根目录路径
        timeout: 单个文件读取超时时间（秒）

    Returns:
        DICOM 文件路径列表
    """
    dicom_files: List[Path] = []
    root = Path(root_path)

    if not root.exists():
        logger.error(f"路径不存在: {root_path}")
        return dicom_files

    if not root.is_dir():
        logger.error(f"路径不是目录: {root_path}")
        return dicom_files

    logger.info(f"开始扫描目录: {root_path}")

    # 跳过的文件扩展名
    skip_extensions = {
        ".txt", ".csv", ".json", ".xml", ".log",
        ".zip", ".tar", ".gz", ".bz2", ".7z",
        ".nii", ".nii.gz", ".img", ".hdr",
        ".exe", ".dll", ".so", ".dylib",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx"
    }

    file_count = 0
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue

        file_count += 1

        # 跳过明显不是 DICOM 的文件
        suffix = file_path.suffix.lower()
        if suffix in skip_extensions:
            continue

        # 检查文件大小（DICOM 文件通常大于 100 字节）
        try:
            stat = file_path.stat()
            if stat.st_size < 100:
                continue
        except Exception:
            continue

        # 检查是否为 DICOM 文件
        if is_dicom_file(file_path, timeout):
            dicom_files.append(file_path)

    logger.info(f"扫描完成，共检查 {file_count} 个文件，找到 {len(dicom_files)} 个 DICOM 文件")
    return dicom_files


def get_metadata(dicom_file: Path, meta_keys: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """
    提取 DICOM 文件的元数据

    Args:
        dicom_file: DICOM 文件路径
        meta_keys: 要提取的元数据键列表，None 则提取默认键

    Returns:
        元数据字典，失败返回 None
    """
    default_keys = [
        "PatientID", "PatientName", "StudyInstanceUID", "StudyID",
        "SeriesInstanceUID", "SeriesDescription", "ProtocolName",
        "AccessionNumber", "AcquisitionNumber", "SliceLocation",
        "InstanceNumber", "AcquisitionTime", "Manufacturer",
        "Rows", "Columns", "ImageOrientationPatient"
    ]

    if meta_keys is None:
        meta_keys = default_keys

    metadata: Dict[str, Any] = {
        "file_path": str(dicom_file),
        "file_name": dicom_file.name
    }

    try:
        ds = pydicom.dcmread(dicom_file, stop_before_pixels=True, force=True)

        # 验证是否为有效的 DICOM 文件
        if not hasattr(ds, 'file_meta') and not hasattr(ds, 'SeriesInstanceUID'):
            logger.debug(f"可能不是有效的 DICOM 文件: {dicom_file}")

        for key in meta_keys:
            value = safe_get(ds, key)
            metadata[key] = value

        # 特殊处理数字类型
        for num_key in ["AcquisitionNumber", "InstanceNumber", "Rows", "Columns"]:
            try:
                if num_key in metadata and metadata[num_key] is not None:
                    val = float(metadata[num_key])
                    if val.is_integer():
                        metadata[num_key] = int(val)
                    else:
                        metadata[num_key] = val
            except (ValueError, TypeError):
                metadata[num_key] = 0 if num_key in ["Rows", "Columns"] else None

        # 特殊处理 SliceLocation
        try:
            if "SliceLocation" in metadata and metadata["SliceLocation"] is not None:
                metadata["SliceLocation"] = float(metadata["SliceLocation"])
        except (ValueError, TypeError):
            metadata["SliceLocation"] = None

        # 处理 AcquisitionTime 格式
        if "AcquisitionTime" in metadata and metadata["AcquisitionTime"]:
            time_str = str(metadata["AcquisitionTime"])
            # 只保留 HHMMSS 格式
            if "." in time_str:
                time_str = time_str.split(".")[0]
            metadata["AcquisitionTime"] = time_str[:6] if len(time_str) >= 6 else time_str

        return metadata

    except InvalidDicomError:
        logger.warning(f"无效的 DICOM 文件: {dicom_file}")
    except Exception as e:
        logger.error(f"读取 DICOM 元数据失败: {dicom_file}, 错误: {str(e)}")

    return None


def filter_in(x: Dict[str, Any]) -> bool:
    """
    根据序列描述和厂商信息过滤序列

    Args:
        x: 序列元数据字典

    Returns:
        True 表示保留，False 表示过滤
    """
    desc = str(x.get("SeriesDescription", "")).lower().strip()
    protocol = str(x.get("ProtocolName", "")).lower().strip()
    manufacturer = str(x.get("Manufacturer", "")).lower().strip()

    # 如果描述和协议名都为空，保留（可能是特殊序列）
    if not desc and not protocol:
        return True

    # -------------------------- 通用过滤规则 --------------------------
    generic_skip_patterns = {
        "localizer", "survey", "3-pl loc", "3-pl ssfse loc", "3-pl loc ssfse",
        "processed images", "screen save", "default ps series", "scout",
        "dose report", "dose info", "dose summary", "save"
    }

    for pattern in generic_skip_patterns:
        if pattern in desc or pattern in protocol:
            logger.debug(f"过滤序列（通用规则）: {desc or protocol}")
            return False

    # -------------------------- 厂商特定过滤规则 --------------------------
    # 提取干净的描述用于精确匹配
    desc_clean = desc.strip().lower()

    # Philips 特定过滤
    if "philips" in manufacturer:
        philips_skip_patterns = {"recon"}
        philips_skip_exact = {"in", "op", "water", "all", "a1", "a2", "a60", "v60", "3min", "8min"}

        for pattern in philips_skip_patterns:
            if pattern in desc or pattern in protocol:
                logger.debug(f"过滤序列（Philips 规则）: {desc}")
                return False

        if desc_clean in philips_skip_exact:
            logger.debug(f"过滤序列（Philips 精确匹配）: {desc}")
            return False

    # GE 特定过滤
    if "ge" in manufacturer or "general electric" in manufacturer:
        ge_skip_patterns = {"orig", "mpr", "refomate", "reformate", "ideal iq", "calibration"}
        ge_skip_exact = {
            "water", "inphase", "outphase", "opposed", "fat", "ip", "op",
            "lava-flex", "lava flex", "lava_ax", "lava_cor"
        }

        for pattern in ge_skip_patterns:
            if pattern in desc or pattern in protocol:
                logger.debug(f"过滤序列（GE 规则）: {desc}")
                return False

        if desc_clean in ge_skip_exact:
            logger.debug(f"过滤序列（GE 精确匹配）: {desc}")
            return False

    # SIEMENS 特定过滤
    if "siemens" in manufacturer:
        siemens_skip_patterns = {"map", "b0map", "b1map", "fieldmap"}
        siemens_skip_exact = {
            "water", "in", "opp", "fat", "inphase", "outphase", "opposed",
            "vibe dixon", "vibe_dixon", "dixon vibe", "t1_vibe_dixon"
        }

        for pattern in siemens_skip_patterns:
            if pattern in desc or pattern in protocol:
                logger.debug(f"过滤序列（SIEMENS 规则）: {desc}")
                return False

        if desc_clean in siemens_skip_exact:
            logger.debug(f"过滤序列（SIEMENS 精确匹配）: {desc}")
            return False

    return True


# -------------------------- 核心数据类 --------------------------
@dataclass
class SeriesData:
    """
    存储单个序列的数据，提供转换为 ITK 图像和保存为 NIfTI 的方法
    """
    files: List[str]
    metadata: Dict[str, Any]
    will_save_file_keys: List[str] = field(default_factory=lambda: ["SeriesDescription", "ProtocolName", "AcquisitionTime"])
    will_save_folder_keys: List[str] = field(default_factory=lambda: ["PatientID", "AccessionNumber"])
    will_save_root_path: Optional[str] = None

    def __post_init__(self):
        """初始化后处理"""
        # 确保文件列表已排序
        self.files = sorted(self.files)

        # 验证文件列表
        if not self.files:
            logger.warning("SeriesData 被创建时没有文件")

    def get_folder_name(self) -> str:
        """
        获取保存文件夹名称

        Returns:
            文件夹名称
        """
        parts = []
        for key in self.will_save_folder_keys:
            value = str(self.metadata.get(key, f"unknown_{key}"))
            # 使用 StudyID 作为 AccessionNumber 的备选
            if key == "AccessionNumber" and (not value or value == "unknown_AccessionNumber"):
                value = str(self.metadata.get("StudyID", "unknown"))
            parts.append(sanitize_file_name(value))

        return os.path.join(*parts) if parts else "unknown"

    def get_file_name(self, index: int, length: int, split_index: int = 0) -> str:
        """
        获取保存文件名

        Args:
            index: 序列索引
            length: 切片数量
            split_index: 多序列拆分标号

        Returns:
            文件名
        """
        parts = [f"{index:02d}", f"L{length:03d}"]

        for key in self.will_save_file_keys:
            value = str(self.metadata.get(key, f"unknown_{key}"))
            if value and value != f"unknown_{key}":
                parts.append(sanitize_file_name(value))

        parts.append(f"{split_index}")
        return "-".join(parts) + ".nii.gz"

    def to_itk_image(self) -> Optional[sitk.Image]:
        """
        转换为 ITK 图像

        Returns:
            ITK 图像对象，失败返回 None
        """
        if not self.files:
            logger.error("没有文件可以转换")
            return None

        # 检查文件是否存在
        existing_files = [f for f in self.files if os.path.exists(f)]
        if not existing_files:
            logger.error(f"所有文件都不存在: {self.files[:3]}...")
            return None

        if len(existing_files) != len(self.files):
            logger.warning(f"部分文件不存在，将使用 {len(existing_files)}/{len(self.files)} 个文件")

        try:
            reader = sitk.ImageSeriesReader()
            reader.SetFileNames(existing_files)
            reader.LoadPrivateTagsOn()
            image = reader.Execute()

            # 设置元数据
            for key, value in self.metadata.items():
                try:
                    if value is not None and str(value):
                        image.SetMetaData(key, str(value))
                except Exception:
                    pass

            return image
        except Exception as e:
            logger.error(f"转换为 ITK 图像失败: {str(e)}")
            return None

    def to_save_nifti(self, index: int = 0, split_index: int = 0) -> Optional[Path]:
        """
        保存为 NIfTI 文件

        Args:
            index: 序列索引
            split_index: 多序列拆分标号

        Returns:
            保存的文件路径，失败返回 None
        """
        if self.will_save_root_path is None:
            logger.error("保存路径未设置")
            return None

        try:
            root_path = Path(self.will_save_root_path)

            # 检查并创建保存目录
            if not root_path.exists():
                root_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"创建保存目录: {root_path}")

            # 检查写入权限
            if not os.access(root_path, os.W_OK):
                logger.error(f"保存路径没有写入权限: {root_path}")
                return None

            folder_name = self.get_folder_name()
            save_dir = root_path / folder_name
            save_dir.mkdir(exist_ok=True, parents=True)

            length = len(self.files)
            file_name = self.get_file_name(index, length, split_index)
            save_path = save_dir / file_name

            # 检查文件是否已存在
            if save_path.exists():
                logger.warning(f"文件已存在，跳过: {save_path}")
                return save_path

            # 转换并保存
            image = self.to_itk_image()
            if image is None:
                return None

            writer = sitk.ImageFileWriter()
            writer.SetFileName(str(save_path))
            writer.UseCompressionOn()
            writer.Execute(image)

            logger.info(f"保存成功: {save_path}")
            return save_path

        except PermissionError:
            logger.error(f"保存 NIfTI 文件失败: 权限不足")
        except Exception as e:
            logger.error(f"保存 NIfTI 文件失败: {str(e)}")
        return None


# -------------------------- 核心分割类 --------------------------
class DicomSeriesSplit:
    """
    DICOM 序列分割核心类
    """

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        n_jobs: int = DEFAULT_N_JOBS,
        backend: Optional[str] = None,
        min_slices: int = DEFAULT_MIN_SLICES,
        skip_desc: Optional[Set[str]] = None,
        filter_func: Optional[Callable[[Dict[str, Any]], bool]] = None,
        meta_keys: Optional[List[str]] = None,
        will_save_file_keys: Optional[List[str]] = None,
        will_save_folder_keys: Optional[List[str]] = None,
        will_save_root_path: Optional[str] = None
    ):
        self.timeout = max(1, timeout)  # 确保至少 1 秒
        self.n_jobs = max(1, n_jobs)    # 确保至少 1 个线程
        self.backend = backend
        self.min_slices = max(1, min_slices)  # 确保至少 1 个切片
        self.skip_desc = {d.lower().strip() for d in (skip_desc or set())}
        self.filter_func = filter_func or filter_in
        self.meta_keys = meta_keys
        self.will_save_file_keys = will_save_file_keys or ["SeriesDescription", "ProtocolName", "AcquisitionTime"]
        self.will_save_folder_keys = will_save_folder_keys or ["PatientID", "AccessionNumber"]
        self.will_save_root_path = will_save_root_path

        # 验证保存路径
        if will_save_root_path and not self._validate_save_path(will_save_root_path):
            logger.warning(f"保存路径可能无效: {will_save_root_path}")

        logger.info(f"DicomSeriesSplit 初始化完成，min_slices={self.min_slices}, timeout={self.timeout}, n_jobs={self.n_jobs}")

    def _validate_save_path(self, path: str) -> bool:
        """验证保存路径是否可写"""
        try:
            p = Path(path)
            if p.exists():
                return os.access(p, os.W_OK)
            # 尝试创建目录
            p.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            logger.error(f"保存路径验证失败: {e}")
            return False

    def _read_metadata_parallel(self, dicom_files: List[Path]) -> List[Dict[str, Any]]:
        """
        并行读取 DICOM 文件元数据

        Args:
            dicom_files: DICOM 文件路径列表

        Returns:
            元数据列表
        """
        metadata_list: List[Dict[str, Any]] = []

        if not dicom_files:
            return metadata_list

        logger.info(f"开始读取 {len(dicom_files)} 个文件的元数据 (使用 {self.n_jobs} 个线程)")
        start_time = time.time()

        # 单线程模式（对于少量文件更高效）
        if len(dicom_files) < 10 or self.n_jobs == 1:
            for file_path in dicom_files:
                metadata = get_metadata(file_path, self.meta_keys)
                if metadata:
                    metadata_list.append(metadata)
        else:
            # 多线程模式
            with ThreadPoolExecutor(max_workers=self.n_jobs) as executor:
                futures = {
                    executor.submit(get_metadata, file, self.meta_keys): file
                    for file in dicom_files
                }

                for future in as_completed(futures):
                    file = futures[future]
                    try:
                        metadata = future.result()
                        if metadata:
                            metadata_list.append(metadata)
                    except Exception as e:
                        logger.error(f"读取文件 {file} 元数据失败: {str(e)}")

        elapsed = time.time() - start_time
        logger.info(f"成功读取 {len(metadata_list)}/{len(dicom_files)} 个文件的元数据，耗时 {elapsed:.2f} 秒")
        return metadata_list

    def _group_by_series(self, metadata_list: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        根据 SeriesInstanceUID 分组

        Args:
            metadata_list: 元数据列表

        Returns:
            分组后的字典，key 为 SeriesInstanceUID，value 为元数据列表
        """
        series_groups: Dict[str, List[Dict[str, Any]]] = {}

        for metadata in metadata_list:
            if not metadata:
                continue

            # 检查是否有有效的 SeriesInstanceUID
            series_uid = str(metadata.get("SeriesInstanceUID", "")).strip()
            if not series_uid or series_uid == "None" or series_uid == "":
                logger.debug(f"文件缺少 SeriesInstanceUID，尝试使用 StudyID+SeriesDescription 组合")
                study_uid = str(metadata.get("StudyInstanceUID", "unknown"))
                series_desc = str(metadata.get("SeriesDescription", "unknown"))
                series_uid = f"{study_uid}_{series_desc}"

            # 检查是否符合过滤规则
            try:
                if not self.filter_func(metadata):
                    continue
            except Exception as e:
                logger.warning(f"过滤函数执行失败: {e}")

            # 检查自定义跳过规则
            desc = str(metadata.get("SeriesDescription", "")).lower().strip()
            if desc in self.skip_desc:
                logger.debug(f"跳过序列（自定义规则）: {desc}")
                continue

            # 添加到分组
            if series_uid not in series_groups:
                series_groups[series_uid] = []
            series_groups[series_uid].append(metadata)

        logger.info(f"分组完成，共 {len(series_groups)} 个序列")
        return series_groups

    def _split_series_by_acquisition_number(self, series_metadata: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        根据 AcquisitionNumber 拆分序列

        Args:
            series_metadata: 序列元数据列表

        Returns:
            拆分后的子序列列表
        """
        if not series_metadata:
            return []

        # 检查是否有足够的 AcquisitionNumber 值
        acq_numbers: Set[int] = set()
        for md in series_metadata:
            acq_num = md.get("AcquisitionNumber")
            if acq_num is not None and isinstance(acq_num, (int, float)):
                acq_numbers.add(int(acq_num))

        if len(acq_numbers) <= 1:
            return [series_metadata]

        # 检查是否应该使用 AcquisitionNumber 拆分（避免同反相位等特殊情况）
        first_md = series_metadata[0]
        desc = str(first_md.get("SeriesDescription", "")).lower()
        protocol = str(first_md.get("ProtocolName", "")).lower()

        in_phase_terms = ["inphase", "in_phase", "in phase", "ip", "water", "in-phase"]
        out_phase_terms = ["outphase", "opposed", "op", "fat", "dixon", "out-phase", "opposed-phase"]

        has_in_phase = any(term in desc or term in protocol for term in in_phase_terms)
        has_out_phase = any(term in desc or term in protocol for term in out_phase_terms)

        if has_in_phase and has_out_phase:
            logger.debug("检测到同反相位序列，不使用 AcquisitionNumber 拆分")
            return [series_metadata]

        # 检查 AcquisitionNumber 分布是否合理
        slices_per_acq: Dict[int, int] = {}
        for md in series_metadata:
            acq_num = md.get("AcquisitionNumber")
            if acq_num is not None:
                try:
                    acq_num_int = int(acq_num)
                    slices_per_acq[acq_num_int] = slices_per_acq.get(acq_num_int, 0) + 1
                except (ValueError, TypeError):
                    pass

        if not slices_per_acq:
            return [series_metadata]

        # 如果某个 AcquisitionNumber 的切片数太少，不拆分
        min_count = min(slices_per_acq.values())
        threshold = max(3, self.min_slices // 2)
        if min_count < threshold:
            logger.debug(f"部分 AcquisitionNumber 切片数太少 (min={min_count}, threshold={threshold})，不拆分")
            return [series_metadata]

        # 执行拆分
        split_groups: Dict[int, List[Dict[str, Any]]] = {}
        for md in series_metadata:
            acq_num = md.get("AcquisitionNumber")
            if acq_num is None:
                acq_num = 0
            try:
                acq_num_int = int(acq_num)
            except (ValueError, TypeError):
                acq_num_int = 0

            if acq_num_int not in split_groups:
                split_groups[acq_num_int] = []
            split_groups[acq_num_int].append(md)

        result = list(split_groups.values())
        logger.debug(f"使用 AcquisitionNumber 拆分，共 {len(result)} 个子序列")
        return result

    def _split_series_by_slice_location(self, series_metadata: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        根据 SliceLocation 拆分序列

        Args:
            series_metadata: 序列元数据列表

        Returns:
            拆分后的子序列列表
        """
        if not series_metadata:
            return []

        # 检查 SliceLocation 是否有效
        valid_locations: List[float] = []
        for md in series_metadata:
            loc = md.get("SliceLocation")
            if loc is not None and isinstance(loc, (int, float)):
                valid_locations.append(float(loc))

        if len(valid_locations) < len(series_metadata) // 2:
            logger.debug(f"有效 SliceLocation 太少 ({len(valid_locations)}/{len(series_metadata)})，不拆分")
            return [series_metadata]

        # 检查是否有重复的 SliceLocation
        location_counts: Dict[float, int] = {}

        for loc in valid_locations:
            rounded_loc = round(loc / SLICE_LOCATION_TOLERANCE) * SLICE_LOCATION_TOLERANCE
            location_counts[rounded_loc] = location_counts.get(rounded_loc, 0) + 1

        # 检查是否有足够多的重复位置
        duplicate_count = sum(1 for cnt in location_counts.values() if cnt > 1)
        if duplicate_count < 5:
            logger.debug(f"重复 SliceLocation 太少 ({duplicate_count})，不拆分")
            return [series_metadata]

        # 执行拆分
        split_groups: Dict[float, List[Dict[str, Any]]] = {}
        for md in series_metadata:
            loc = md.get("SliceLocation")
            if loc is None:
                continue
            rounded_loc = round(float(loc) / SLICE_LOCATION_TOLERANCE) * SLICE_LOCATION_TOLERANCE
            if rounded_loc not in split_groups:
                split_groups[rounded_loc] = []
            split_groups[rounded_loc].append(md)

        # 如果没有足够的分组，返回原序列
        if len(split_groups) <= 1:
            return [series_metadata]

        # 按 InstanceNumber 排序每个分组
        for group in split_groups.values():
            group.sort(key=lambda x: int(x.get("InstanceNumber", 0) or 0))

        # 过滤掉切片数太少的分组
        result = []
        for group in split_groups.values():
            if len(group) >= self.min_slices:
                result.append(group)

        # 如果没有足够的分组，返回原序列
        if len(result) <= 1:
            logger.debug(f"拆分后有效子序列太少 ({len(result)})，返回原序列")
            return [series_metadata]

        logger.debug(f"使用 SliceLocation 拆分，共 {len(result)} 个子序列")
        return result

    def _process_series(self, series_metadata: List[Dict[str, Any]]) -> List[SeriesData]:
        """
        处理单个序列，包括拆分和过滤

        Args:
            series_metadata: 序列元数据列表

        Returns:
            SeriesData 对象列表
        """
        result: List[SeriesData] = []

        # 检查切片数量
        if len(series_metadata) < self.min_slices:
            logger.debug(f"序列切片数 {len(series_metadata)} 小于最小值 {self.min_slices}，跳过")
            return result

        # 尝试按 AcquisitionNumber 拆分
        split_by_acq = self._split_series_by_acquisition_number(series_metadata)

        # 如果没有拆分成功，尝试按 SliceLocation 拆分
        if len(split_by_acq) == 1:
            split_by_loc = self._split_series_by_slice_location(series_metadata)
            split_groups = split_by_loc
        else:
            split_groups = split_by_acq

        # 处理每个拆分后的子序列
        for split_idx, group in enumerate(split_groups):
            if len(group) < self.min_slices:
                logger.debug(f"子序列切片数 {len(group)} 小于最小值 {self.min_slices}，跳过")
                continue

            # 获取文件路径列表
            files = [md.get("file_path", "") for md in group if md.get("file_path")]
            if not files:
                logger.warning("子序列没有有效的文件路径")
                continue

            # 使用第一个文件的元数据作为序列元数据
            base_metadata = group[0].copy()
            base_metadata["slice_count"] = len(group)
            base_metadata["split_index"] = split_idx

            # 创建 SeriesData 对象
            try:
                series_data = SeriesData(
                    files=files,
                    metadata=base_metadata,
                    will_save_file_keys=self.will_save_file_keys,
                    will_save_folder_keys=self.will_save_folder_keys,
                    will_save_root_path=self.will_save_root_path
                )
                result.append(series_data)
            except Exception as e:
                logger.error(f"创建 SeriesData 失败: {e}")

        return result

    def __call__(self, root_path: str) -> List[SeriesData]:
        """
        处理 DICOM 文件

        Args:
            root_path: DICOM 文件根目录

        Returns:
            SeriesData 对象列表
        """
        if not root_path or not isinstance(root_path, str):
            logger.error(f"无效的根路径: {root_path}")
            return []

        logger.info(f"开始处理目录: {root_path}")
        start_time = time.time()

        # 步骤 1: 获取所有 DICOM 文件
        dicom_files = get_dicom_file(root_path, self.timeout)
        if not dicom_files:
            logger.warning("未找到 DICOM 文件")
            return []

        # 步骤 2: 读取元数据
        metadata_list = self._read_metadata_parallel(dicom_files)
        if not metadata_list:
            logger.warning("未读取到任何元数据")
            return []

        # 步骤 3: 按 SeriesInstanceUID 分组
        series_groups = self._group_by_series(metadata_list)
        if not series_groups:
            logger.warning("没有符合条件的序列")
            return []

        # 步骤 4: 处理每个序列
        all_series: List[SeriesData] = []
        for series_uid, series_metadata in series_groups.items():
            try:
                series_list = self._process_series(series_metadata)
                all_series.extend(series_list)
            except Exception as e:
                logger.error(f"处理序列 {series_uid} 失败: {e}")

        elapsed = time.time() - start_time
        logger.info(f"处理完成，共得到 {len(all_series)} 个有效序列，耗时 {elapsed:.2f} 秒")
        return all_series


# -------------------------- GUI 界面 --------------------------
class GuiLogHandler:
    """GUI 日志处理器"""

    def __init__(self, text_widget: tk.Text, log_queue: queue.Queue):
        self.text_widget = text_widget
        self.log_queue = log_queue
        self._running = True

    def write(self, message: str):
        """将日志消息放入队列"""
        if self._running:
            try:
                self.log_queue.put(message, block=False)
            except queue.Full:
                pass

    def flush(self):
        pass

    def stop(self):
        self._running = False


class DicomApp:
    """
    DICOM Splitter GUI 界面
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DICOM Splitter v1.76")
        self.root.geometry("900x700")
        self.root.resizable(True, True)

        # 变量
        self.dicom_path_var = tk.StringVar()
        self.save_path_var = tk.StringVar()
        self.min_slices_var = tk.IntVar(value=DEFAULT_MIN_SLICES)
        self.timeout_var = tk.IntVar(value=DEFAULT_TIMEOUT)
        self.n_jobs_var = tk.IntVar(value=DEFAULT_N_JOBS)
        self.running = False

        # 日志队列
        self.log_queue: queue.Queue = queue.Queue(maxsize=1000)
        self.gui_handler: Optional[GuiLogHandler] = None
        self._log_after_id: Optional[str] = None

        # 设置窗口样式
        self._setup_style()

        # 创建界面
        self._create_widgets()

        # 重定向日志到 GUI
        self._setup_logging()

        # 启动日志更新循环
        self._update_log()

        logger.info("DICOM Splitter v1.76 启动成功")

    def _setup_style(self):
        """设置界面样式"""
        style = ttk.Style()
        style.theme_use("clam")

        # 配置样式
        style.configure("Title.TLabel", font=("Arial", 12, "bold"))
        style.configure("Header.TFrame", background="#f0f0f0")
        style.configure("Log.TFrame", background="#1e1e1e")

    def _create_widgets(self):
        """创建界面组件"""
        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # 标题
        title_label = ttk.Label(main_frame, text="DICOM 序列分割与 NIfTI 转换工具 v1.76", style="Title.TLabel")
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 20))

        # DICOM 路径选择
        ttk.Label(main_frame, text="DICOM Root Path:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.dicom_path_var, width=60).grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=5)
        ttk.Button(main_frame, text="Browse", command=self._browse_dicom_path).grid(row=1, column=2, pady=5)

        # 保存路径选择
        ttk.Label(main_frame, text="NIFTI Save Path:").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.save_path_var, width=60).grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5, padx=5)
        ttk.Button(main_frame, text="Browse", command=self._browse_save_path).grid(row=2, column=2, pady=5)

        # 参数设置
        param_frame = ttk.LabelFrame(main_frame, text="参数设置", padding="5")
        param_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)

        ttk.Label(param_frame, text="Minimum Slices:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        ttk.Spinbox(param_frame, from_=1, to=1000, textvariable=self.min_slices_var, width=10).grid(row=0, column=1, padx=(0, 20))

        ttk.Label(param_frame, text="Timeout (s):").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        ttk.Spinbox(param_frame, from_=1, to=60, textvariable=self.timeout_var, width=10).grid(row=0, column=3, padx=(0, 20))

        ttk.Label(param_frame, text="Threads:").grid(row=0, column=4, sticky=tk.W, padx=(0, 5))
        ttk.Spinbox(param_frame, from_=1, to=16, textvariable=self.n_jobs_var, width=10).grid(row=0, column=5)

        # 运行按钮
        self.run_button = ttk.Button(main_frame, text="Run", command=self._run_processing)
        self.run_button.grid(row=4, column=0, columnspan=3, pady=10)

        # 日志区域
        ttk.Label(main_frame, text="Processing Log:").grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(10, 5))

        log_frame = ttk.Frame(main_frame, style="Log.TFrame")
        log_frame.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        main_frame.grid_rowconfigure(6, weight=1)
        main_frame.grid_columnconfigure(1, weight=1)

        # 日志文本框
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        # 滚动条
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _setup_logging(self):
        """设置日志重定向到 GUI"""
        self.gui_handler = GuiLogHandler(self.log_text, self.log_queue)

        # 添加 GUI 处理器 - 使用唯一标识避免重复
        logger.add(
            self.gui_handler,
            format="{time:HH:mm:ss} | {level: <8} | {message}",
            level="INFO",
            filter=lambda record: True,
            enqueue=True  # 使用队列避免线程问题
        )

    def _update_log(self):
        """更新日志显示"""
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.insert(tk.END, message)
                # 限制日志行数
                lines = int(self.log_text.index('end-1c').split('.')[0])
                if lines > 1000:
                    self.log_text.delete(1.0, f"{lines - 900}.0")
                self.log_text.see(tk.END)
        except queue.Empty:
            pass

        self._log_after_id = self.root.after(100, self._update_log)

    def _browse_dicom_path(self):
        """浏览 DICOM 路径"""
        path = filedialog.askdirectory(title="Select DICOM Root Directory")
        if path:
            self.dicom_path_var.set(path)

    def _browse_save_path(self):
        """浏览保存路径"""
        path = filedialog.askdirectory(title="Select NIfTI Save Directory")
        if path:
            self.save_path_var.set(path)

    def _validate_inputs(self) -> Tuple[bool, str]:
        """验证输入参数"""
        dicom_path = self.dicom_path_var.get().strip()
        save_path = self.save_path_var.get().strip()

        if not dicom_path:
            return False, "请选择 DICOM 根目录"

        if not save_path:
            return False, "请选择 NIfTI 保存目录"

        if not os.path.isdir(dicom_path):
            return False, "DICOM 路径不是有效的目录"

        if not os.path.exists(save_path):
            try:
                os.makedirs(save_path, exist_ok=True)
            except Exception as e:
                return False, f"无法创建保存目录: {e}"

        # 检查保存路径是否可写
        try:
            test_file = os.path.join(save_path, ".test_write_permission")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except Exception:
            return False, "保存目录没有写入权限，请检查权限设置"

        # 验证数值参数
        if self.min_slices_var.get() < 1:
            return False, "最小切片数必须大于等于 1"

        if self.timeout_var.get() < 1:
            return False, "超时时间必须大于等于 1 秒"

        return True, ""

    def _run_processing_thread(self):
        """在后台线程中运行处理"""
        try:
            dicom_path = self.dicom_path_var.get().strip()
            save_path = self.save_path_var.get().strip()

            logger.info("=" * 60)
            logger.info("开始处理")
            logger.info(f"DICOM 路径: {dicom_path}")
            logger.info(f"保存路径: {save_path}")
            logger.info(f"最小切片数: {self.min_slices_var.get()}")
            logger.info(f"超时时间: {self.timeout_var.get()} 秒")
            logger.info(f"并行线程: {self.n_jobs_var.get()}")
            logger.info("=" * 60)

            # 创建分割器
            splitter = DicomSeriesSplit(
                timeout=self.timeout_var.get(),
                n_jobs=self.n_jobs_var.get(),
                min_slices=self.min_slices_var.get(),
                will_save_root_path=save_path
            )

            # 处理文件
            start_time = time.time()
            split_list = splitter(dicom_path)
            end_time = time.time()

            # 保存 NIfTI
            if split_list:
                logger.info(f"开始保存 {len(split_list)} 个序列...")
                saved_count = 0
                failed_count = 0

                # 按病人/检查号分组，重置索引
                folder_groups: Dict[str, List[SeriesData]] = {}
                for series in split_list:
                    folder_name = series.get_folder_name()
                    if folder_name not in folder_groups:
                        folder_groups[folder_name] = []
                    folder_groups[folder_name].append(series)

                for folder_name, series_list in folder_groups.items():
                    for idx, series in enumerate(series_list):
                        saved_path = series.to_save_nifti(index=idx)
                        if saved_path:
                            saved_count += 1
                        else:
                            failed_count += 1

                logger.info(f"处理完成！成功保存 {saved_count} 个，失败 {failed_count} 个")
            else:
                logger.warning("没有找到符合条件的序列")

            logger.info(f"总耗时: {end_time - start_time:.2f} 秒")

            # 在主线程中显示完成消息
            self.root.after(0, lambda: self._show_completion_message(len(split_list)))

        except Exception as e:
            logger.error(f"处理失败: {str(e)}", exc_info=True)
            self.root.after(0, lambda: self._show_error_message(str(e)))
        finally:
            self.root.after(0, self._reset_ui)

    def _show_completion_message(self, count: int):
        """显示完成消息"""
        messagebox.showinfo("Success", f"处理完成！\n共保存 {count} 个 NIfTI 文件。")

    def _show_error_message(self, error: str):
        """显示错误消息"""
        messagebox.showerror("Error", f"处理失败:\n{error}")

    def _reset_ui(self):
        """重置 UI 状态"""
        self.running = False
        self.run_button.config(state=tk.NORMAL)

    def _run_processing(self):
        """运行处理流程"""
        if self.running:
            return

        # 验证输入
        is_valid, error_msg = self._validate_inputs()
        if not is_valid:
            messagebox.showerror("Error", error_msg)
            return

        self.running = True
        self.run_button.config(state=tk.DISABLED)
        self.log_text.delete(1.0, tk.END)

        # 在后台线程中运行处理，避免阻塞 GUI
        thread = Thread(target=self._run_processing_thread, daemon=True)
        thread.start()

    def on_closing(self):
        """窗口关闭处理"""
        if self._log_after_id:
            self.root.after_cancel(self._log_after_id)
        if self.gui_handler:
            self.gui_handler.stop()
        self.root.destroy()


# -------------------------- 主程序入口 --------------------------
def main():
    """主程序入口"""
    try:
        root = tk.Tk()
        app = DicomApp(root)

        # 设置关闭处理
        root.protocol("WM_DELETE_WINDOW", app.on_closing)

        root.mainloop()
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"程序崩溃: {str(e)}", exc_info=True)
        messagebox.showerror("Fatal Error", f"程序发生严重错误:\n{str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
