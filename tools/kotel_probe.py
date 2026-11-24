#!/usr/bin/env python3
"""Сбор ответов сервера ReglerPro для анализа (только чтение, ничего не меняет).

Запросы: login_user, get_data_sets, get_all_dev, get_big_contr, get_big_contr_check.
Логин, пароль, номер дома, названия домов и идентификаторы устройств в выводе заменяются
метками, длинные графики сокращаются, поэтому результат можно прикладывать к обращению.

Настройка через переменные окружения (пароль в команду не вписывайте):
    KOTEL_URL       адрес сервера (по умолчанию https://app.reglerpro.ru)
    KOTEL_LOGIN     логин
    KOTEL_PASSWORD  пароль (если не задан, будет запрошен)
    KOTEL_HOME_ID   номер дома (нужен для devices, controller, check, all)

Примеры:
    python kotel_probe.py login
    python kotel_probe.py homes
    python kotel_probe.py all --out probe.txt
    python kotel_probe.py devices --timeout 5
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

PATH = "/php/taptop.php"
SHORTEN_KEYS = {"graf", "time_for_graf"}


def post(base, payload, timeout):
    """(статус, секунды, тип содержимого, текст ответа, описание ошибки)."""
    request = urllib.request.Request(
        base.rstrip("/") + PATH, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return (response.status, time.monotonic() - started,
                    response.headers.get("Content-Type", ""), body, "")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")
        return err.code, time.monotonic() - started, err.headers.get("Content-Type", ""), body, ""
    except (socket.timeout, TimeoutError):
        return None, time.monotonic() - started, "", "", f"НЕТ ОТВЕТА за {timeout} с"
    except urllib.error.URLError as err:
        reason = err.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return None, time.monotonic() - started, "", "", f"НЕТ ОТВЕТА за {timeout} с"
        return None, time.monotonic() - started, "", "", f"ошибка соединения: {reason}"


def parse(body):
    try:
        return json.loads(body)
    except ValueError:
        return None


def collect_ids(value, found):
    """Идентификаторы устройств и домов из любого ответа."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("id_name", "tstatDtId") and isinstance(item, (str, int)) and str(item):
                found.add(str(item))
            if key == "homes" and isinstance(item, dict):
                found.update(str(home_id) for home_id in item)
            collect_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_ids(item, found)


def shorten(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in SHORTEN_KEYS:
                data = parse(item) if isinstance(item, str) else item
                count = len(data) if isinstance(data, list) else "?"
                out[key] = f"<график: {count} точек, сокращён>"
            else:
                out[key] = shorten(item)
        return out
    if isinstance(value, list):
        return [shorten(item) for item in value]
    return value


def make_scrubber(secrets):
    """Секрет -> метка; длинные первыми."""
    pairs = sorted(((s, label) for s, label in secrets if len(s) >= 3), key=lambda p: -len(p[0]))

    def scrub(text):
        for secret, label in pairs:
            text = text.replace(secret, label)
        return text
    return scrub


def main():
    parser = argparse.ArgumentParser(description="Сбор ответов сервера ReglerPro (только чтение)")
    parser.add_argument("command", choices=["login", "homes", "devices", "controller", "check", "all"])
    parser.add_argument("device_id", nargs="?", help="идентификатор контроллера (controller, check)")
    parser.add_argument("--timeout", type=float, default=15.0, help="ожидание ответа, с")
    parser.add_argument("--out", help="сохранить вывод в файл")
    parser.add_argument("--raw", action="store_true",
                        help="не скрывать данные (результат НЕЛЬЗЯ отправлять никому)")
    args = parser.parse_args()

    base = os.environ.get("KOTEL_URL", "https://app.reglerpro.ru")
    login = os.environ.get("KOTEL_LOGIN") or input("Логин: ")
    password = os.environ.get("KOTEL_PASSWORD") or getpass.getpass("Пароль: ")
    home_id = os.environ.get("KOTEL_HOME_ID", "")
    if args.command in ("devices", "controller", "check", "all") and not home_id:
        sys.exit("Для этой команды задайте KOTEL_HOME_ID")

    def payload(action, **extra):
        data = {"action": action, "login": login, "password": password,
                "action_value1": "", "action_value2": ""}
        data.update(extra)
        return data

    requests = []          # (заголовок, полезная нагрузка без секретов для вывода, ответ)

    def run(title, action, **extra):
        status, seconds, ctype, body, error = post(base, payload(action, **extra), args.timeout)
        requests.append((title, action, status, seconds, ctype, body, error))
        return parse(body)

    if args.command in ("login", "all"):
        run("login_user", "login_user")
    if args.command in ("homes", "all"):
        run("get_data_sets", "get_data_sets")
    devices = None
    if args.command in ("devices", "all"):
        devices = run("get_all_dev", "get_all_dev", action_value1=home_id)
    if args.command in ("controller", "check") and args.device_id:
        if args.command == "controller":
            run(f"get_big_contr {args.device_id}", "get_big_contr", id_name=args.device_id)
        else:
            run(f"get_big_contr_check {args.device_id}", "get_big_contr_check",
                id_name=args.device_id, userId=home_id)
    if args.command == "all" and isinstance(devices, list):
        for device in devices:
            if isinstance(device, dict) and device.get("type") == "contr" and device.get("id_name"):
                dev_id = str(device["id_name"])
                run(f"get_big_contr {dev_id}", "get_big_contr", id_name=dev_id)
                run(f"get_big_contr_check {dev_id}", "get_big_contr_check",
                    id_name=dev_id, userId=home_id)

    # метки для секретов: логин, пароль, номер дома, названия домов и все идентификаторы
    ids = set()
    for *_, body, _error in requests:
        collect_ids(parse(body), ids)
    ids.discard(home_id)
    secrets = [(password, "***"), (login, "<login>"), (home_id, "<home_id>")]
    secrets += [(device_id, f"<id-{n}>") for n, device_id in enumerate(sorted(ids), 1)]
    homes = {}
    for title, action, status, seconds, ctype, body, error in requests:
        data = parse(body)
        if action == "get_data_sets" and isinstance(data, dict) and isinstance(data.get("homes"), dict):
            homes = {str(k): str(v) for k, v in data["homes"].items()}
    secrets += [(name, f"<home-name-{n}>") for n, name in enumerate(sorted(set(homes.values())), 1)]
    scrub = (lambda text: text) if args.raw else make_scrubber(secrets)

    lines = [f"Сервер: {scrub(base) if args.raw else base}",
             f"Время: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for title, action, status, seconds, ctype, body, error in requests:
        lines.append(f"=== {scrub(title)} ===")
        lines.append(f"HTTP: {status if status is not None else '-'} | {seconds:.1f} с | {ctype or '-'}")
        if error:
            lines.append(error)
        elif body:
            data = parse(body)
            if data is None:
                lines.append("(не JSON) " + scrub(body[:500]))
            else:
                shown = data if args.raw else shorten(data)
                lines.append(scrub(json.dumps(shown, ensure_ascii=False, indent=1)))
        else:
            lines.append("(пустой ответ)")
        lines.append("")
    text = "\n".join(lines)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"Сохранено: {args.out}")


if __name__ == "__main__":
    main()
