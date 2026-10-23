#!/usr/bin/env python3
"""Проверка переводов интеграции ReglerPro Controller.

Запуск из корня репозитория:

    python scripts/check_i18n.py

Проверяется:
* strings.json совпадает с translations/en.json;
* ru.json повторяет структуру en.json (все ключи переведены);
* у каждой сущности есть name;
* каждый translation_key из Python-кода есть в strings.json;
* все службы из services.py описаны в переводах.

Скрипт не требует Home Assistant и внешних библиотек — только stdlib.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "kotel"

PLATFORM_FILES = {
    "sensor.py": "sensor",
    "number.py": "number",
    "select.py": "select",
    "text.py": "text",
    "button.py": "button",
    "switch.py": "switch",
    "climate.py": "climate",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def flatten(value, prefix="") -> dict[str, object]:
    """Плоская карта ключей JSON: {'entity.sensor.x.name': '...'}."""
    result: dict[str, object] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            result.update(flatten(item, f"{prefix}.{key}" if prefix else key))
    else:
        result[prefix] = value
    return result


def collect_keys(files: list[Path]) -> set[str]:
    """Собрать translation_key и ключи из таблиц описаний."""
    keys: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.keyword)
                and node.arg == "translation_key"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                keys.add(node.value.value)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                add_table_keys(node.value, keys)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        add_table_keys(item.value, keys)
    return keys


def add_table_keys(value, keys: set[str]) -> None:
    """Добавить вторые элементы кортежей и первые элементы словарей."""
    if isinstance(value, (ast.Tuple, ast.List)):
        for element in value.elts:
            if isinstance(element, ast.Tuple) and len(element.elts) >= 2:
                second = element.elts[1]
                if isinstance(second, ast.Constant) and isinstance(second.value, str):
                    keys.add(second.value)
    elif isinstance(value, ast.Dict):
        for item in value.values:
            if isinstance(item, ast.Dict):
                add_table_keys(item, keys)
            elif isinstance(item, ast.Tuple) and item.elts:
                first = item.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)


def main() -> int:
    strings = load(COMPONENT / "strings.json")
    en = load(COMPONENT / "translations" / "en.json")
    ru = load(COMPONENT / "translations" / "ru.json")
    errors: list[str] = []

    if strings != en:
        errors.append("strings.json и translations/en.json различаются")

    flat_en = flatten(en)
    flat_ru = flatten(ru)
    missing_ru = sorted(set(flat_en) - set(flat_ru))
    extra_ru = sorted(set(flat_ru) - set(flat_en))
    if missing_ru:
        errors.append(f"ru.json: нет ключей — {', '.join(missing_ru[:20])}")
    if extra_ru:
        errors.append(f"ru.json: лишние ключи — {', '.join(extra_ru[:20])}")

    for platform, items in strings.get("entity", {}).items():
        for key, item in items.items():
            if not isinstance(item, dict) or not item.get("name"):
                errors.append(f"entity.{platform}.{key}: нет name")

    code_keys = collect_keys([COMPONENT / name for name in PLATFORM_FILES])
    for file_name, platform in PLATFORM_FILES.items():
        # Ключи конкретной платформы проверяем по её файлу.
        platform_keys = collect_keys([COMPONENT / file_name])
        known = set(strings.get("entity", {}).get(platform, {}))
        missing = sorted(platform_keys - known)
        if missing:
            errors.append(
                f"{file_name}: translation_key отсутствует в entity.{platform}: "
                + ", ".join(missing)
            )

    # Все ключи из JSON должны где-то использоваться (кроме динамического расписания).
    unused = sorted(
        key
        for platform, items in strings.get("entity", {}).items()
        for key in items
        if key not in code_keys and not key.startswith("schedule_")
    )
    if unused:
        errors.append("Ключи без ссылки в коде: " + ", ".join(unused))

    services_tree = ast.parse((COMPONENT / "services.py").read_text(encoding="utf-8"))
    service_names: set[str] = set()
    for node in ast.walk(services_tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SERVICE_NAMES"
            for target in node.targets
        ):
            if isinstance(node.value, (ast.Tuple, ast.List)):
                service_names = {
                    element.value
                    for element in node.value.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
    described = set(strings.get("services", {}))
    missing_services = sorted(service_names - described)
    extra_services = sorted(described - service_names)
    if missing_services:
        errors.append("Службы без перевода: " + ", ".join(missing_services))
    if extra_services:
        errors.append("Переводы без службы: " + ", ".join(extra_services))

    if errors:
        print("Ошибки переводов:")
        for error in errors:
            print(" -", error)
        return 1

    print(
        "Переводы в порядке: "
        f"{sum(len(v) for v in strings['entity'].values())} сущностей, "
        f"{len(strings['services'])} служб."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
