from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from .config import load_sources, source_to_mapping
from .models import SourceConfig


class SourceStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def all(self) -> dict[str, SourceConfig]:
        return load_sources(self.directory)

    def save(self, source: SourceConfig, *, previous_id: str | None = None) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{source.source_id}.yml"
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{source.source_id}-", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(
                    source_to_mapping(source),
                    handle,
                    allow_unicode=True,
                    sort_keys=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

        if previous_id and previous_id != source.source_id:
            self.delete(previous_id)

    def delete(self, source_id: str) -> None:
        for suffix in (".yml", ".yaml"):
            try:
                (self.directory / f"{source_id}{suffix}").unlink()
            except FileNotFoundError:
                pass
