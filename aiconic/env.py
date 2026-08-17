"""Чтение .env — один источник для всех точек входа.

Было три реализации: main.py разбирал файл сам, login.py вообще не разбирал
(и падал с «задайте TG_API_ID», хотя всё было заполнено), eval_answers.py
вытаскивал один ключ инлайном. Три поведения из одного файла — это и был баг.

Зависимость ради двадцати строк не нужна.
"""

from __future__ import annotations

import os
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent


def загрузить(файл: Path | None = None) -> None:
    """Подставляет в окружение то, чего там ещё нет.

    setdefault, а не присваивание: переменная, заданная руками в консоли,
    должна побеждать файл — иначе не переопределить ключ на один прогон.
    """
    файл = файл or КОРЕНЬ / ".env"
    if not файл.exists():
        return
    for строка in файл.read_text(encoding="utf-8").splitlines():
        строка = строка.strip()
        if not строка or строка.startswith("#") or "=" not in строка:
            continue
        # partition, а не split: строка сессии — base64 и содержит '=' внутри
        # и в хвосте. split('=') разорвал бы её и дал битую сессию.
        ключ, _, значение = строка.partition("=")
        os.environ.setdefault(ключ.strip(), значение.strip().strip('"').strip("'"))
