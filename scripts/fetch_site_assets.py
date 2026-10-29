#!/usr/bin/env python3
"""Скачивание JS-файлов сайта https://app.reglerpro.ru/ в папку assets.

Запуск из корня репозитория:

    python scripts/fetch_site_assets.py            # базовый набор
    python scripts/fetch_site_assets.py --depth 2  # рекурсивно искать ссылки в скачанных JS

Файлы сохраняются в assets/site/*.js (внешние CDN-библиотеки — в
assets/site/cdn/). Полученный код нужен для последующего анализа API
приложения ReglerPro. Скрипт использует только stdlib.

Источники:
* <script src="..."> в главной странице /index.html;
* динамически подключаемые файлы (script.src = "*.js?t=..."):
  functions.js, app.js, sets.js, reglog.js, climate.js, script.js, charts.js;
* service worker sw.js.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BASE_URL = "https://app.reglerpro.ru/"
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "site"

# Файлы, подключаемые приложением динамически (обнаружены анализом functions.js).
DYNAMIC_SCRIPTS = [
    "functions.js",
    "app.js",
    "sets.js",
    "reglog.js",
    "climate.js",
    "script.js",
    "charts.js",
    "sw.js",
]

# Внешние скрипты из <head> (оставляем в подпапке cdn, чтобы не путать с кодом приложения).
CDN_SCRIPTS = [
    "https://cdn.jsdelivr.net/npm/chart.js",
    "https://hammerjs.github.io/dist/hammer.min.js",
    "https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom/dist/chartjs-plugin-zoom.min.js",
]

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"'>]+)[\"']", re.IGNORECASE)
DYNAMIC_SRC_RE = re.compile(r"\.src\s*=\s*[`\"']([^`\"']*\.js(?:\?[^`\"']*)?)[`\"']", re.IGNORECASE)


def strip_query(url: str) -> str:
    return url.split("?", 1)[0]


def fetch(url: str) -> bytes:
    req = Request(url, headers=UA)
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def is_same_host(url: str) -> bool:
    return urlparse(BASE_URL).netloc == urlparse(url).netloc


def download(url: str, dest: Path) -> tuple[str, int, int]:
    """Скачивает url в dest. Возвращает (имя файла, статус, размер)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = fetch(url)
    except Exception as exc:  # noqa: BLE001
        return url, 1, 0
    dest.write_bytes(data)
    return url, 0, len(data)


def collect_scripts(html_text: str) -> list[str]:
    found = []
    for m in SCRIPT_SRC_RE.finditer(html_text):
        url = urljoin(BASE_URL, m.group(1))
        if url not in found:
            found.append(url)
    for m in DYNAMIC_SRC_RE.finditer(html_text):
        url = urljoin(BASE_URL, strip_query(m.group(1)))
        if url not in found:
            found.append(url)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--depth",
        type=int,
        default=0,
        help="дополнительно искать ссылки на .js внутри уже скачанных файлов (0 — только базовый набор)",
    )
    args = parser.parse_args()

    print(f"Скачивание в {ASSETS_DIR}")

    # Базовый набор: страница + динамические + CDN.
    page_text = b""
    try:
        page_text = fetch(BASE_URL)
        print(f"OK  / (главная страница, {len(page_text)} байт)")
    except Exception as exc:  # noqa: BLE001
        print(f"ERR главная страница: {exc}")
        return 1

    urls: list[str] = []
    urls += collect_scripts(page_text.decode("utf-8", errors="replace"))
    for name in DYNAMIC_SCRIPTS:
        url = urljoin(BASE_URL, name)
        if url not in urls:
            urls.append(url)
    urls += [u for u in CDN_SCRIPTS if u not in urls]

    # Рекурсивный поиск итераций: JS может подключать другие JS.
    queue = list(urls)
    processed: set[str] = set()
    while queue and (args.depth > 0 or len(processed) == 0):
        url = queue.pop(0)
        if url in processed:
            continue
        processed.add(url)
        if not args.depth and not is_same_host(url):
            # На первом проходе (depth=0) CDN-скрипты тоже скачиваем отдельно,
            # но их содержимое не просматриваем.
            pass
        try:
            data = fetch(url)
        except Exception:  # noqa: BLE001
            continue
        for m in DYNAMIC_SRC_RE.finditer(data.decode("utf-8", errors="replace")):
            candidate = urljoin(url, strip_query(m.group(1)))
            if candidate not in urls:
                urls.append(candidate)
        if args.depth > 0:
            queue += [urljoin(url, strip_query(m.group(1))) for m in SCRIPT_SRC_RE.finditer(
                data.decode("utf-8", errors="replace")
            )]

    # Скачивание.
    ok, fail = 0, 0
    for url in sorted(set(urls)):
        path = urlparse(url).path.lstrip("/")
        if urlparse(BASE_URL).netloc != urlparse(url).netloc:
            path = "cdn/" + path.strip("/").replace("/", "__")
        dest = ASSETS_DIR / (path or "index.js")
        fname, status, size = download(url, dest)
        if status == 0:
            print(f"OK  {fname} ({size} байт) -> {dest.relative_to(ASSETS_DIR)}")
            ok += 1
        else:
            print(f"ERR {fname}")
            fail += 1

    print(f"\nГотово: {ok} скачано, {fail} ошибок.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
