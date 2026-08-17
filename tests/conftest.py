from __future__ import annotations

import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ))

from aiconic import guards                      # noqa: E402
from aiconic.app import Входящее, Дирижёр, Лог   # noqa: E402
from aiconic.llm import FakeProvider, Ответ      # noqa: E402
from aiconic.salon import Салон                  # noqa: E402
from aiconic.sender import Sender                # noqa: E402

# 2026-08-13 — четверг. Работают Нино (10-18) и Гиорги (11-19).
ЧЕТВЕРГ = date(2026, 8, 13)
# Время тоже фиксируем, иначе тесты зависят от часа прогона: прошедшие окна
# больше не считаются свободными, и в 21:32 сегодняшних окон не остаётся вовсе.
# 09:00 — до открытия, значит весь день впереди.
УТРО = 9 * 60


@pytest.fixture
def каталог(tmp_path: Path) -> Path:
    """Копия data/ в tmp: тесты пишут файлы, но не трогают проектные."""
    dst = tmp_path / "data"
    dst.mkdir()
    for имя in ("price.yaml", "schedule.yaml", "bookings.yaml"):
        shutil.copy(КОРЕНЬ / "data" / имя, dst / имя)
    return dst


@pytest.fixture
def салон(каталог: Path) -> Салон:
    return Салон(каталог)


class ФейкТранспорт:
    """Вместо Telegram. Ничего не шлёт, всё запоминает."""

    def __init__(self) -> None:
        self.отправленное: list[tuple[int, str]] = []

    async def отправить(self, чат_id: int, текст: str) -> None:
        self.отправленное.append((чат_id, текст))


@pytest.fixture
def транспорт() -> ФейкТранспорт:
    return ФейкТранспорт()


@pytest.fixture
def лог() -> Лог:
    return Лог(путь=None)


@pytest.fixture
def отправитель(транспорт, салон, лог) -> Sender:
    return Sender(транспорт, салон, белый_список={7}, лог=лог)


def входящее(текст: str, *, чат: int = 7, сдвиг_сек: int = 10, **kw) -> Входящее:
    return Входящее(
        чат_id=чат,
        текст=текст,
        дата=datetime.now(timezone.utc) + timedelta(seconds=сдвиг_сек),
        **kw,
    )


@pytest.fixture
def сделать_дирижёра(салон, отправитель, лог):
    def фабрика(ответы=None, история=None, белый={7}, наблюдение=False, пауза=0.0):
        приём = guards.Приём(
            белый_список=белый,
            старт=datetime.now(timezone.utc),
            режим_наблюдения=наблюдение,
        )
        провайдер = FakeProvider(ответы or [])
        д = Дирижёр(
            салон=салон,
            провайдер=провайдер,
            sender=отправитель,
            приём=приём,
            лог=лог,
            история=(история or (lambda чат, n: [])),
            сегодня=lambda: ЧЕТВЕРГ,
            сейчас_минут=lambda: УТРО,
            пауза_группировки=пауза,
        )
        return д, провайдер

    return фабрика
