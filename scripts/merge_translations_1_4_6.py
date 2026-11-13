"""Добавляет ключи версии 1.4.6 в translations/en.json и translations/ru.json.

Запуск из корня репозитория:
    python scripts/merge_translations_1_4_6.py

Скрипт можно запускать повторно: уже существующие ключи просто перезаписываются
теми же значениями. Остальное содержимое файлов не меняется.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "kotel"
ADDITIONS = Path(__file__).resolve().parent.parent / "translations_additions"


def merge(target: dict, extra: dict) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = value


for lang in ("en", "ru"):
    path = COMPONENT / "translations" / f"{lang}.json"
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    data = json.loads(raw)
    merge(data, json.loads((ADDITIONS / f"{lang}.json").read_text(encoding="utf-8")))
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    print("обновлён", path.relative_to(ROOT))
