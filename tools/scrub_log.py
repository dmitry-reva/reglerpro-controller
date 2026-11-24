#!/usr/bin/env python3
"""Подготовить журнал Home Assistant к отправке: оставить строки интеграции kotel и
заменить логин, пароль, номер дома и идентификаторы устройств метками.

    python scrub_log.py home-assistant.log --secret ЛОГИН --secret ПАРОЛЬ --secret НОМЕР_ДОМА ^
        --id 6008555528819 --id 3631427318216 --out kotel_log.txt

Секреты передавайте через переменные окружения, чтобы они не остались в истории команд:
KOTEL_LOGIN, KOTEL_PASSWORD, KOTEL_HOME_ID (они подставляются автоматически).
Параметр --all сохраняет весь журнал, а не только строки kotel.
"""

import argparse
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--secret", action="append", default=[], help="значение, которое скрыть")
    parser.add_argument("--id", action="append", default=[], help="идентификатор устройства")
    parser.add_argument("--all", action="store_true", help="не фильтровать строки")
    parser.add_argument("--out", default="kotel_log.txt")
    args = parser.parse_args()

    pairs = []
    env = (("KOTEL_PASSWORD", "***"), ("KOTEL_LOGIN", "<login>"), ("KOTEL_HOME_ID", "<home_id>"))
    for name, label in env:
        if os.environ.get(name):
            pairs.append((os.environ[name], label))
    pairs += [(value, f"<secret-{n}>") for n, value in enumerate(args.secret, 1)]
    pairs += [(value, f"<id-{n}>") for n, value in enumerate(args.id, 1)]
    pairs = sorted((p for p in pairs if len(p[0]) >= 3), key=lambda p: -len(p[0]))
    if not pairs:
        sys.exit("Не задан ни один секрет: задайте KOTEL_LOGIN, KOTEL_PASSWORD, KOTEL_HOME_ID "
                 "или параметры --secret / --id")

    timestamp = re.compile(r"^\d{4}-\d{2}-\d{2} \d")
    kept, keep = [], False
    with open(args.log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if timestamp.match(line):             # новая запись журнала
                keep = args.all or "kotel" in line.lower()
            # строки без метки времени (трассировка, многострочное сообщение) — продолжение записи
            if keep:
                for secret, label in pairs:
                    line = line.replace(secret, label)
                kept.append(line)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.writelines(kept)
    print(f"Строк сохранено: {len(kept)} -> {args.out}")


if __name__ == "__main__":
    main()
