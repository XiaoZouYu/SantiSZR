from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import json
from pathlib import Path
import shutil
import uuid

from santiszr.config.settings import AppSettings
from santiszr.core.paths import resolve_runtime_paths, sanitize_filename


AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class AssetCategory(str, Enum):
    audio = "audio"
    reference_video = "reference_video"
    background_music = "background_music"


@dataclass(slots=True, frozen=True)
class ManagedAsset:
    asset_id: str
    category: AssetCategory
    display_name: str
    original_filename: str
    relative_path: str
    path: str
    added_at: str
    size_bytes: int


class MediaLibrary:
    def __init__(self, settings: AppSettings) -> None:
        runtime_paths = resolve_runtime_paths(settings)
        self._root = runtime_paths.data / "media-library"
        self._manifest_path = self._root / "manifest.json"
        self._root.mkdir(parents=True, exist_ok=True)
        for category in AssetCategory:
            self._category_dir(category).mkdir(parents=True, exist_ok=True)

    def list_assets(self, category: AssetCategory) -> list[ManagedAsset]:
        records = self._load_records()
        assets: list[ManagedAsset] = []
        changed = False
        normalized_records: list[dict[str, object]] = []

        for record in records:
            if str(record.get("category", "")).strip() != category.value:
                normalized_records.append(record)
                continue

            asset = self._record_to_asset(record)
            if asset is None:
                changed = True
                continue

            normalized_records.append(
                {
                    "asset_id": asset.asset_id,
                    "category": asset.category.value,
                    "display_name": asset.display_name,
                    "original_filename": asset.original_filename,
                    "relative_path": asset.relative_path,
                    "added_at": asset.added_at,
                    "size_bytes": asset.size_bytes,
                }
            )
            assets.append(asset)

        if changed:
            self._save_records(normalized_records)

        return sorted(assets, key=lambda item: (item.added_at, item.display_name.lower()), reverse=True)

    def list_paths(self, category: AssetCategory) -> list[str]:
        return [asset.path for asset in self.list_assets(category)]

    def import_file(self, category: AssetCategory, source_path: str | Path) -> ManagedAsset:
        source = Path(source_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"File not found: {source}")
        self._validate_suffix(category, source.suffix)

        category_dir = self._category_dir(category)
        base_name = sanitize_filename(source.stem, fallback=category.value)
        suffix = source.suffix.lower()
        destination = category_dir / f"{base_name}{suffix}"
        while destination.exists():
            destination = category_dir / f"{base_name}-{uuid.uuid4().hex[:8]}{suffix}"

        shutil.copy2(source, destination)

        asset = ManagedAsset(
            asset_id=uuid.uuid4().hex,
            category=category,
            display_name=destination.stem,
            original_filename=source.name,
            relative_path=destination.relative_to(self._root).as_posix(),
            path=str(destination.resolve()),
            added_at=datetime.now(UTC).isoformat(),
            size_bytes=destination.stat().st_size,
        )

        records = self._load_records()
        records.append(
            {
                "asset_id": asset.asset_id,
                "category": asset.category.value,
                "display_name": asset.display_name,
                "original_filename": asset.original_filename,
                "relative_path": asset.relative_path,
                "added_at": asset.added_at,
                "size_bytes": asset.size_bytes,
            }
        )
        self._save_records(records)
        return asset

    def delete_asset(self, category: AssetCategory, asset_id: str) -> ManagedAsset | None:
        records = self._load_records()
        kept_records: list[dict[str, object]] = []
        removed_asset: ManagedAsset | None = None

        for record in records:
            if (
                removed_asset is None
                and str(record.get("category", "")).strip() == category.value
                and str(record.get("asset_id", "")).strip() == asset_id
            ):
                removed_asset = self._record_to_asset(record)
                continue
            kept_records.append(record)

        if removed_asset is None:
            return None

        asset_path = Path(removed_asset.path)
        if asset_path.exists():
            asset_path.unlink()
        self._save_records(kept_records)
        return removed_asset

    def category_label(self, category: AssetCategory) -> str:
        labels = {
            AssetCategory.audio: "音频",
            AssetCategory.reference_video: "参考视频",
            AssetCategory.background_music: "背景音乐",
        }
        return labels[category]

    def file_filter(self, category: AssetCategory) -> str:
        filters = {
            AssetCategory.audio: "Audio Files (*.wav *.mp3 *.m4a *.aac *.flac *.ogg)",
            AssetCategory.reference_video: "Video Files (*.mp4 *.mov *.avi *.mkv *.webm)",
            AssetCategory.background_music: "Audio Files (*.wav *.mp3 *.m4a *.aac *.flac *.ogg)",
        }
        return filters[category]

    def _validate_suffix(self, category: AssetCategory, suffix: str) -> None:
        normalized = suffix.lower()
        allowed = {
            AssetCategory.audio: AUDIO_SUFFIXES,
            AssetCategory.reference_video: VIDEO_SUFFIXES,
            AssetCategory.background_music: AUDIO_SUFFIXES,
        }[category]
        if normalized not in allowed:
            raise ValueError(f"Unsupported {category.value} format: {suffix}")

    def _category_dir(self, category: AssetCategory) -> Path:
        names = {
            AssetCategory.audio: "audio",
            AssetCategory.reference_video: "reference-videos",
            AssetCategory.background_music: "background-music",
        }
        return self._root / names[category]

    def _load_records(self) -> list[dict[str, object]]:
        if not self._manifest_path.exists():
            return []
        try:
            raw = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(raw, dict):
            return []
        records = raw.get("assets", [])
        return [record for record in records if isinstance(record, dict)]

    def _save_records(self, records: list[dict[str, object]]) -> None:
        payload = {"version": 1, "assets": records}
        self._manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _record_to_asset(self, record: dict[str, object]) -> ManagedAsset | None:
        relative_path = str(record.get("relative_path", "")).strip()
        if not relative_path:
            return None

        try:
            category = AssetCategory(str(record.get("category", "")).strip())
        except ValueError:
            return None

        absolute_path = (self._root / relative_path).resolve()
        if not absolute_path.exists() or not absolute_path.is_file():
            return None

        display_name = str(record.get("display_name", "")).strip() or absolute_path.stem
        original_filename = str(record.get("original_filename", "")).strip() or absolute_path.name
        added_at = str(record.get("added_at", "")).strip() or datetime.now(UTC).isoformat()
        size_bytes = int(record.get("size_bytes") or absolute_path.stat().st_size)

        return ManagedAsset(
            asset_id=str(record.get("asset_id", "")).strip() or uuid.uuid4().hex,
            category=category,
            display_name=display_name,
            original_filename=original_filename,
            relative_path=relative_path,
            path=str(absolute_path),
            added_at=added_at,
            size_bytes=size_bytes,
        )
