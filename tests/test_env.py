"""Чтение .env.

Эти тесты написаны после живого бага: login.py не читал .env и печатал
«задайте TG_API_ID», хотя ключ был заполнен. Главный тест здесь — последний:
он отвергает ровно эту поломку, а не проверяет парсер, который и так работал.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from aiconic import env

КОРЕНЬ = Path(__file__).resolve().parent.parent
ТОЧКИ_ВХОДА = ["login.py", "main.py", "tools/eval_answers.py"]


@pytest.fixture
def чистое_окружение(monkeypatch):
    for k in ("A_КЛЮЧ", "B_КЛЮЧ", "TG_SESSION", "ПУСТОЙ"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def записать(tmp_path: Path, текст: str) -> Path:
    файл = tmp_path / ".env"
    файл.write_text(текст, encoding="utf-8")
    return файл


def test_значения_попадают_в_окружение(tmp_path, чистое_окружение):
    env.загрузить(записать(tmp_path, "A_КЛЮЧ=раз\nB_КЛЮЧ=два\n"))
    assert os.environ["A_КЛЮЧ"] == "раз"
    assert os.environ["B_КЛЮЧ"] == "два"


def test_заданное_руками_побеждает_файл(tmp_path, чистое_окружение):
    """Иначе нельзя переопределить ключ на один прогон из консоли."""
    чистое_окружение.setenv("A_КЛЮЧ", "из_консоли")
    env.загрузить(записать(tmp_path, "A_КЛЮЧ=из_файла\n"))
    assert os.environ["A_КЛЮЧ"] == "из_консоли"


def test_строка_сессии_с_равно_внутри_не_рвётся(tmp_path, чистое_окружение):
    """Настоящая строка сессии — base64: '=' есть и внутри, и в хвосте.

    split('=') вместо partition отдал бы 'AQD3' и битую сессию — вход
    провалился бы с невнятной ошибкой уже на живом аккаунте.
    """
    сессия = "AQD3nQ==vJ8k=="
    env.загрузить(записать(tmp_path, f"TG_SESSION={сессия}\n"))
    assert os.environ["TG_SESSION"] == сессия


def test_комментарии_и_пустые_строки_пропускаются(tmp_path, чистое_окружение):
    env.загрузить(записать(tmp_path, "# комментарий=не ключ\n\n   \nA_КЛЮЧ=раз\n"))
    assert os.environ["A_КЛЮЧ"] == "раз"
    assert "# комментарий" not in os.environ
    assert "комментарий" not in os.environ


def test_пустое_значение_не_маскирует_отсутствие(tmp_path, чистое_окружение):
    """TG_SESSION= в файле должен читаться как пусто, а не как заполненный.

    Точки входа проверяют переменные через `if not os.environ.get(...)`,
    поэтому пустая строка обязана оставаться ложной.
    """
    env.загрузить(записать(tmp_path, "ПУСТОЙ=\n"))
    assert os.environ["ПУСТОЙ"] == ""
    assert not os.environ.get("ПУСТОЙ")


def test_кавычки_снимаются(tmp_path, чистое_окружение):
    env.загрузить(записать(tmp_path, 'A_КЛЮЧ="раз"\nB_КЛЮЧ=\'два\'\n'))
    assert os.environ["A_КЛЮЧ"] == "раз"
    assert os.environ["B_КЛЮЧ"] == "два"


def test_отсутствие_файла_не_падает(tmp_path):
    env.загрузить(tmp_path / "нет-такого.env")


@pytest.mark.parametrize("точка", ТОЧКИ_ВХОДА)
def test_каждая_точка_входа_читает_env(точка):
    """Заслон против пойманного бага.

    Ровно это и было сломано: парсер существовал в main.py, а login.py его не
    звал. Проверяем вызов, а не упоминание в docstring.
    """
    код = (КОРЕНЬ / точка).read_text(encoding="utf-8")
    assert re.search(r"env\.загрузить\s*\(", код), (
        f"{точка} не вызывает env.загрузить() — .env не будет прочитан, "
        f"и точка входа соврёт, что переменные не заданы"
    )


@pytest.mark.parametrize("точка", ТОЧКИ_ВХОДА)
def test_свой_парсер_env_не_возвращается(точка):
    """Три копии парсера — это и был источник расхождения (§17)."""
    код = (КОРЕНЬ / точка).read_text(encoding="utf-8")
    assert 'startswith("#")' not in код, (
        f"{точка} снова разбирает .env сам — читать должен только aiconic/env.py"
    )
