"""Форма запроса к модели. DoD-пункт 13 финального чертежа.

Пробел найден при сверке реализации с чертежом 2026-08-13: пункт «ответ с
цепочкой рассуждений не уходит клиенту» был специфицирован, но не покрыт ничем.

Почему это заслон, а не косметика: при `reasoning_format="raw"` gpt-oss склеивает
рассуждения прямо в `message.content`, БЕЗ тегов — отфильтровать их потом нечем.
Единственная защита — не получать их вовсе. Значит проверять надо сам запрос.
"""

from __future__ import annotations

import json

import pytest

from aiconic.llm import СХЕМА_ОТВЕТА, CerebrasProvider, ОшибкаПровайдера


class ФейкОтвет:
    def __init__(self, содержимое: str) -> None:
        self.choices = [type("C", (), {"message": type("M", (), {"content": содержимое})})]


class ФейкCompletions:
    def __init__(self, содержимое: str) -> None:
        self.содержимое = содержимое
        self.вызовы: list[dict] = []

    def create(self, **kw):
        self.вызовы.append(kw)
        return ФейкОтвет(self.содержимое)


def сделать_провайдера(содержимое: str = '{"reply":"ок","needs_human":false,'
                                        '"reason":"","booking":null}',
                       seed=None) -> CerebrasProvider:
    п = CerebrasProvider.__new__(CerebrasProvider)   # без сети и без ключа
    п.модель = "gpt-oss-120b"
    п.seed = seed
    completions = ФейкCompletions(содержимое)
    п._клиент = type("К", (), {"chat": type("Ч", (), {"completions": completions})})
    return п


def test_рассуждения_не_запрашиваются():
    """ГЛАВНЫЙ. Отвергает reasoning_format='raw' — при нём цепочка мыслей
    приходит внутри текста ответа и уезжает клиенту."""
    п = сделать_провайдера()
    п.ответить("система", [{"role": "user", "content": "привет"}])

    kw = п._клиент.chat.completions.вызовы[-1]
    assert kw["reasoning_format"] == "hidden", (
        "при 'raw' рассуждения склеиваются в content без тегов — отфильтровать нечем"
    )


def test_вывод_строго_по_схеме():
    """Отвергает запрос без strict: тогда форма ответа — пожелание, не гарантия."""
    п = сделать_провайдера()
    п.ответить("система", [])
    схема = п._клиент.chat.completions.вызовы[-1]["response_format"]
    assert схема["type"] == "json_schema"
    assert схема["json_schema"]["strict"] is True
    assert схема["json_schema"]["schema"] is СХЕМА_ОТВЕТА


def test_потолок_на_длину_ответа():
    """Иначе модель может прислать простыню в переписку."""
    п = сделать_провайдера()
    п.ответить("система", [])
    assert п._клиент.chat.completions.вызовы[-1]["max_completion_tokens"] > 0


def test_системный_промпт_идёт_первым():
    п = сделать_провайдера()
    п.ответить("ПРАВИЛА САЛОНА", [{"role": "user", "content": "привет"}])
    сообщения = п._клиент.chat.completions.вызовы[-1]["messages"]
    assert сообщения[0] == {"role": "system", "content": "ПРАВИЛА САЛОНА"}
    assert сообщения[1]["content"] == "привет"


def test_seed_передаётся_только_когда_задан():
    """Отвергает передачу seed=None: на обычной работе фиксировать нечего."""
    без = сделать_провайдера()
    без.ответить("с", [])
    assert "seed" not in без._клиент.chat.completions.вызовы[-1]

    с = сделать_провайдера(seed=42)
    с.ответить("с", [])
    assert с._клиент.chat.completions.вызовы[-1]["seed"] == 42


def test_ответ_не_по_схеме_становится_ошибкой_провайдера():
    """Клиенту нельзя отправить мусор: сбой обязан стать ОшибкойПровайдера,
    а не долететь до Sender как текст."""
    п = сделать_провайдера("это не json")
    with pytest.raises(ОшибкаПровайдера):
        п.ответить("с", [])


def test_схема_требует_все_четыре_поля():
    """Отвергает схему, где booking необязателен: тогда его молчаливое
    отсутствие не отличить от null."""
    assert set(СХЕМА_ОТВЕТА["required"]) == {
        "reply", "needs_human", "reason", "booking"
    }
    assert СХЕМА_ОТВЕТА["additionalProperties"] is False
    json.dumps(СХЕМА_ОТВЕТА)   # схема обязана быть сериализуемой
