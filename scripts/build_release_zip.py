#!/usr/bin/env python3
"""Собрать HACS zip-архив интеграции ReglerPro Controller.

HACS с ``"zip_release": true`` скачивает asset из GitHub Releases и
распаковывает его **непосредственно в** ``/config/custom_components/kotel``.
Поэтому в корне архива должны лежать файлы интеграции (``__init__.py``,
``manifest.json``, ``brand/``, ``translations/`` …), а не папка
``custom_components/kotel``.

Пример:

    python scripts/build_release_zip.py --output dist/kotel.zip --expected-version v1.4.1
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

DEFAULT_SOURCE = Path("custom_components/kotel")
DEFAULT_OUTPUT = Path("dist/kotel.zip")

EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}


def normalize_version(value: str) -> str:
    """Привести тег ``v1.4.1`` / ``refs/tags/v1.4.1`` к ``1.4.1``."""
    version = value.strip()
    if version.startswith("refs/tags/"):
        version = version.removeprefix("refs/tags/")
    return version.removeprefix("v")


def iter_files(source: Path) -> list[Path]:
    """Файлы интеграции в стабильном порядке, без кэшей и мусора."""
    files = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(source).as_posix())


def build(source: Path, output: Path, expected_version: str | None = None) -> int:
    """Собрать архив и проверить его структуру. Возвращает число файлов."""
    if not source.is_dir():
        raise SystemExit(f"Нет каталога интеграции: {source}")
    if not (source / "manifest.json").is_file():
        raise SystemExit(f"Нет manifest.json в {source}")

    files = iter_files(source)
    if not files:
        raise SystemExit(f"В {source} нет файлов")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            arcname = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        if "manifest.json" not in names:
            raise SystemExit("В архиве нет manifest.json в корне")
        if any(name.startswith("custom_components/") for name in names):
            raise SystemExit(
                "В архиве есть префикс custom_components/ — HACS распакует его "
                "в custom_components/kotel/custom_components/…"
            )
        if "hacs.json" in names:
            raise SystemExit("hacs.json не должен попадать в архив интеграции")

        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("domain") != source.name:
            raise SystemExit(
                f"domain={manifest.get('domain')!r} не совпадает с именем каталога {source.name!r}"
            )
        if expected_version:
            expected = normalize_version(expected_version)
            actual = str(manifest.get("version", ""))
            if actual != expected:
                raise SystemExit(
                    f"version в manifest.json ({actual}) не совпадает с тегом ({expected})"
                )

    return len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help=f"каталог интеграции (по умолчанию {DEFAULT_SOURCE})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"путь к архиву (по умолчанию {DEFAULT_OUTPUT})")
    parser.add_argument("--expected-version", default=None,
                        help="ожидаемая версия из тега, например v1.4.1")
    args = parser.parse_args()

    count = build(args.source, args.output, args.expected_version)
    print(f"Собран {args.output}: {count} файлов, версия "
          f"{json.loads((args.source / 'manifest.json').read_text(encoding='utf-8'))['version']}")
    print("Структура: файлы интеграции в корне архива, префикса custom_components/ нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
