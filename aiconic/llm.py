"""LLMProvider: один интерфейс, несколько реализаций.

Вводится с первого коммита намеренно — иначе провайдер расползётся по коду
и смена станет неделей работы. Cerebras на тесты (⚠️ нужна привязанная карта,
постоянно бесплатного тарифа нет), Claude Haiku 4.5 в продакшн.

Модель возвращает не голый текст, а структуру:

    {"reply": "...", "needs_human": false, "reason": "", "booking": null|{...}}

Это даёт эскалацию в два слоя: наш список триггеров ловит явное, флаг модели —
то, что списком не описать (торг, странный запрос, жалоба).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

СХЕМА_ОТВЕТА = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "needs_human": {"type": "boolean"},
        "reason": {"type": "string"},
        "booking": {
            "type": ["object", "null"],
            "properties": {
                "мастер": {"type": "integer"},
                "дата": {"type": "string"},
                "время": {"type": "string"},
                "услуга": {"type": "string"},
            },
            "required": ["мастер", "дата", "время", "услуга"],
            "additionalProperties": False,
        },
    },
    "required": ["reply", "needs_human", "reason", "booking"],
    "additionalProperties": False,
}


@dataclass
class Ответ:
    reply: str
    needs_human: bool = False
    reason: str = ""
    booking: dict | None = None

    @classmethod
    def из_json(cls, текст: str) -> "Ответ":
        данные = json.loads(текст)
        b = данные.get("booking")
        return cls(
            reply=(данные.get("reply") or "").strip(),
            needs_human=bool(данные.get("needs_human")),
            reason=(данные.get("reason") or "").strip(),
            booking=b if isinstance(b, dict) else None,
        )


class LLMProvider(Protocol):
    имя: str

    def ответить(self, системный: str, сообщения: list[dict]) -> Ответ: ...


class ОшибкаПровайдера(Exception):
    """Провайдер не ответил. Клиенту НИЧЕГО не отправляем — только лог."""


class CerebrasProvider:
    """Только на тесты.

    ⚠️ Постоянно бесплатного тарифа у Cerebras нет: «If you skip adding a payment
    method at sign-up, Playground and API access remain inactive until you do».
    Привязка карты даёт $5 кредитов, которые сгорают через 30 дней. Без карты
    любой запрос возвращает 402 payment_required.

    Лимиты пробного тарифа: 5 запросов/мин, 30K токенов/мин, 1M токенов/сутки.
    """

    имя = "cerebras"

    def __init__(
        self,
        api_key: str | None = None,
        модель: str = "gpt-oss-120b",
        seed: int | None = None,
    ) -> None:
        # ВАЖНО: пакет ставится как cerebras_cloud_sdk,
        # но импортируется как cerebras.cloud.sdk — `import cerebras_cloud_sdk` падает.
        from cerebras.cloud.sdk import Cerebras

        ключ = api_key or os.environ.get("CEREBRAS_API_KEY")
        if not ключ:
            raise ОшибкаПровайдера("не задан CEREBRAS_API_KEY")
        self._клиент = Cerebras(api_key=ключ)
        self.модель = модель
        self.seed = seed

    def ответить(self, системный: str, сообщения: list[dict]) -> Ответ:
        try:
            r = self._клиент.chat.completions.create(
                model=self.модель,
                messages=[{"role": "system", "content": системный}, *сообщения],
                # hidden — иначе gpt-oss склеивает рассуждения прямо в текст ответа,
                # без тегов, и клиент получил бы цепочку мыслей.
                reasoning_format="hidden",
                reasoning_effort="low",
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "salon_reply",
                        "schema": СХЕМА_ОТВЕТА,
                        "strict": True,
                    },
                },
                max_completion_tokens=700,
                **({"seed": self.seed} if self.seed is not None else {}),
            )
            текст = r.choices[0].message.content or ""
        except Exception as e:  # 429, сеть, всё остальное
            raise ОшибкаПровайдера(f"{type(e).__name__}: {e}") from e

        try:
            return Ответ.из_json(текст)
        except (json.JSONDecodeError, TypeError) as e:
            raise ОшибкаПровайдера(f"ответ не по схеме: {e}") from e


    def остаток_квоты(self) -> dict[str, int]:
        """Сколько токенов и запросов осталось в сутках — из заголовков ответа.

        Cerebras отдаёт `x-ratelimit-remaining-tokens-day` на каждом ответе, то
        есть остаток можно СПРОСИТЬ, а не оценивать. Оценка за три дня
        расходилась трижды (106K → 196K → 239K за прогон), и один раз я чуть не
        запустил прогон на 330K, когда в сутках оставалось 41K: он бы сжёг
        остаток и объявил себя несостоявшимся.

        Стоит один короткий запрос (~50 токенов). Пустой словарь значит «узнать
        не удалось» — тогда решать вызывающему, а не молча считать, что всё есть.
        """
        try:
            сырой = self._клиент.chat.completions.with_raw_response.create(
                model=self.модель,
                messages=[{"role": "user", "content": "."}],
                max_completion_tokens=1,
            )
        except Exception:
            return {}
        ответ: dict[str, int] = {}
        for имя, ключ in (
            ("x-ratelimit-remaining-tokens-day", "токенов_в_сутки"),
            ("x-ratelimit-remaining-requests-day", "запросов_в_сутки"),
            ("x-ratelimit-remaining-tokens-minute", "токенов_в_минуту"),
        ):
            значение = сырой.headers.get(имя)
            if значение is not None and str(значение).isdigit():
                ответ[ключ] = int(значение)
        return ответ


class FakeProvider:
    """Для тестов: отдаёт заранее заданное, ничего не зовёт по сети."""

    имя = "fake"

    def __init__(self, ответы: list[Ответ | Exception] | None = None) -> None:
        self.ответы = list(ответы or [])
        self.вызовы: list[tuple[str, list[dict]]] = []

    def ответить(self, системный: str, сообщения: list[dict]) -> Ответ:
        self.вызовы.append((системный, сообщения))
        if not self.ответы:
            return Ответ(reply="ок")
        следующий = self.ответы.pop(0)
        if isinstance(следующий, Exception):
            raise следующий
        return следующий
