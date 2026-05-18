from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


_PACKAGE_DIR = Path(__file__).resolve().parent
_DLL_DIRECTORY_HANDLES: list[Any] = []


def _resolve_upstream_package_dir() -> Path:
    for entry in sys.path:
        try:
            candidate = Path(entry).resolve() / "ctranslate2"
        except Exception:
            continue
        if candidate == _PACKAGE_DIR:
            continue
        if candidate.exists() and any(candidate.glob("_ext*.pyd")):
            return candidate
    raise ImportError("Upstream ctranslate2 package directory is unavailable.")


def _project_cuda_bin_dir() -> Path:
    return _PACKAGE_DIR.parents[2] / "tools" / "nvidia" / "cuda" / "bin"


def _configure_windows_dll_search(upstream_package_dir: Path) -> None:
    if os.name != "nt":
        return

    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return

    candidate_dirs = []
    cuda_path = os.environ.get("CUDA_PATH", "").strip()
    if cuda_path:
        candidate_dirs.append(Path(cuda_path) / "bin")
    candidate_dirs.append(_project_cuda_bin_dir())
    candidate_dirs.append(upstream_package_dir)

    seen: set[str] = set()
    for directory in candidate_dirs:
        if not directory.exists():
            continue
        directory_text = str(directory.resolve())
        if directory_text in seen:
            continue
        seen.add(directory_text)
        try:
            _DLL_DIRECTORY_HANDLES.append(add_dll_directory(directory_text))
        except OSError:
            continue
        _prepend_cuda_bin_to_path(directory)


def _prepend_cuda_bin_to_path(directory: Path) -> None:
    if not (directory / "cublas64_12.dll").exists():
        return

    directory_text = str(directory.resolve())
    current_path = os.environ.get("PATH", "")
    path_entries = current_path.split(os.pathsep) if current_path else []
    normalized_entries = {entry.lower() for entry in path_entries}
    if directory_text.lower() in normalized_entries:
        return
    os.environ["PATH"] = f"{directory_text}{os.pathsep}{current_path}" if current_path else directory_text


def _load_extension_module(upstream_package_dir: Path) -> ModuleType:
    module_name = f"{__name__}._ext"
    cached_module = sys.modules.get(module_name)
    if cached_module is not None:
        return cached_module

    extension_path = next(upstream_package_dir.glob("_ext*.pyd"), None)
    if extension_path is None:
        raise ImportError("ctranslate2 native extension is unavailable.")

    spec = importlib.util.spec_from_file_location(module_name, extension_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load ctranslate2 native extension from {extension_path}.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_version(upstream_package_dir: Path) -> str:
    version_path = upstream_package_dir / "version.py"
    namespace: dict[str, Any] = {}
    exec(version_path.read_text(encoding="utf-8"), namespace)
    return str(namespace.get("__version__", "0.0.0"))


_UPSTREAM_PACKAGE_DIR = _resolve_upstream_package_dir()
_configure_windows_dll_search(_UPSTREAM_PACKAGE_DIR)
_ext = _load_extension_module(_UPSTREAM_PACKAGE_DIR)
__version__ = _load_version(_UPSTREAM_PACKAGE_DIR)

AsyncGenerationResult = _ext.AsyncGenerationResult
AsyncScoringResult = _ext.AsyncScoringResult
AsyncTranslationResult = _ext.AsyncTranslationResult
DataType = _ext.DataType
Device = _ext.Device
Encoder = _ext.Encoder
EncoderForwardOutput = _ext.EncoderForwardOutput
ExecutionStats = _ext.ExecutionStats
GenerationResult = _ext.GenerationResult
GenerationStepResult = _ext.GenerationStepResult
Generator = _ext.Generator
MpiInfo = _ext.MpiInfo
ScoringResult = _ext.ScoringResult
StorageView = _ext.StorageView
TranslationResult = _ext.TranslationResult
Translator = _ext.Translator
contains_model = _ext.contains_model
get_cuda_device_count = _ext.get_cuda_device_count
get_supported_compute_types = _ext.get_supported_compute_types
set_random_seed = _ext.set_random_seed

from . import models
from .logging import get_log_level, set_log_level

__all__ = [
    "__version__",
    "AsyncGenerationResult",
    "AsyncScoringResult",
    "AsyncTranslationResult",
    "DataType",
    "Device",
    "Encoder",
    "EncoderForwardOutput",
    "ExecutionStats",
    "GenerationResult",
    "GenerationStepResult",
    "Generator",
    "MpiInfo",
    "ScoringResult",
    "StorageView",
    "TranslationResult",
    "Translator",
    "contains_model",
    "get_cuda_device_count",
    "get_log_level",
    "get_supported_compute_types",
    "models",
    "set_log_level",
    "set_random_seed",
]
