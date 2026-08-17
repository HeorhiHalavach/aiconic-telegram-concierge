"""Единственный модуль, который знает про Telegram.

Если telethon импортируется где-то ещё — заслон «отправка только через Sender»
обойдён. Это проверяется тестом test_import_boundary.

Работа идёт через сессию аккаунта салона: программа входит в аккаунт как ещё
одно устройство. Администратор продолжает пользоваться тем же аккаунтом со
своего телефона.

Что этот модуль НЕ делает намеренно: не вызывает send_read_acknowledge — пусть
непрочитанные у администратора ведут себя как обычно, мы в это не вмешиваемся.

✅ Проверено живьём 2026-08-13: **непрочитанные у администратора НЕ исчезают
после нашего ответа.** Значит на его телефоне остаётся значок на чатах, которые
дирижёр уже закрыл. Это следствие решения не вызывать send_read_acknowledge, и
оно осознанное: пометить прочитанным мы можем в одну строку, но тогда
администратор потеряет способ увидеть, что в чате что-то было.

⚠️ Проактивная отправка в этапе ЕСТЬ — напоминания за день и за 2 часа
(решение 2026-08-13). Отправляет их не этот модуль напрямую, а цикл в main.py
через Sender: единственная точка выхода остаётся единственной.
"""

from __future__ import annotations

from typing import Callable

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from .app import Входящее
from .канал import Диалог


class TelegramКанал:
    имя = "telegram"

    def __init__(
        self,
        *,
        строка_сессии: str,
        api_id: int,
        api_hash: str,
        белый_список: set[int],
    ) -> None:
        self.белый_список = set(белый_список)
        self.клиент = TelegramClient(
            StringSession(строка_сессии),
            api_id,
            api_hash,
            # catch_up по умолчанию False, но мы и не полагаемся на флаг:
            # отсечка по дате в guards.Приём не зависит от поведения библиотеки.
            catch_up=False,
            flood_sleep_threshold=60,
        )

    # --- транспорт для Sender ---

    async def отправить(self, чат_id: int, текст: str) -> None:
        try:
            await self.клиент.send_message(чат_id, текст)
        except FloodWaitError as e:
            # короткие паузы telethon отсиживает сам (flood_sleep_threshold),
            # сюда попадают только длинные — молчим и даём знать в лог вызывающего
            raise RuntimeError(f"FloodWait {e.seconds}s") from e

    # --- лента диалогов для окна администратора (этап 2) ---

    async def диалоги(self, сколько: int = 50) -> list[Диалог]:
        """Лента одним запросом — это принципиально, а не оптимизация.

        У чтения истории лимит около 10 запросов за 30 секунд. Если бы лента
        собиралась по чату за запрос, страница администратора выбивала бы лимит
        на десятом чате и дальше показывала пустоту. `get_dialogs()` отдаёт
        последнее сообщение каждого чата сразу.

        Групповые чаты отбрасываются: салон работает в личных, а в группах мы
        намеренно не отвечаем (`guards.Приём`), и в ленте им делать нечего.
        """
        итог: list[Диалог] = []
        for д in await self.клиент.get_dialogs(limit=сколько):
            if not getattr(д, "is_user", False):
                continue
            сообщение = getattr(д, "message", None)
            итог.append(
                Диалог(
                    чат_id=int(д.id),
                    имя=str(getattr(д, "name", "") or ""),
                    последнее=(getattr(сообщение, "text", None) or ""),
                    когда=getattr(сообщение, "date", None),
                    непрочитанных=int(getattr(д, "unread_count", 0) or 0),
                    канал=self.имя,
                    наше_последним=bool(getattr(сообщение, "out", False)),
                )
            )
        return итог

    # --- история чата: контекст берём из Telegram, своей БД нет ---

    async def история(self, чат_id: int, сколько: int) -> list[dict]:
        сообщения = await self.клиент.get_messages(
            чат_id, limit=сколько, reverse=True  # от старых к новым!
        )
        итог: list[dict] = []
        for m in сообщения:
            текст = (getattr(m, "text", None) or "")
            итог.append(
                {
                    "текст": текст,
                    "наше": bool(getattr(m, "out", False)),
                    "дата": getattr(m, "date", None),
                }
            )
        return итог

    # --- приём ---

    def подключить(self, обработчик: Callable[[Входящее], "object"]) -> None:
        @self.клиент.on(
            events.NewMessage(
                chats=list(self.белый_список) or None,
                incoming=True,
                func=lambda e: e.is_private,
            )
        )
        async def _(событие) -> None:  # pragma: no cover — проверяется живьём
            m = событие.message
            автор = ""
            try:
                s = await событие.get_sender()
                автор = getattr(s, "username", None) or getattr(s, "first_name", "") or ""
            except Exception:
                pass
            await обработчик(
                Входящее(
                    чат_id=событие.chat_id,
                    текст=(getattr(m, "text", None) or ""),
                    дата=m.date,                      # tz-aware UTC
                    личный=bool(событие.is_private),
                    исходящее=bool(getattr(m, "out", False)),
                    есть_вложение=bool(getattr(m, "media", None)),
                    автор=автор,
                )
            )

    async def запустить(self, после_подключения=None) -> None:  # pragma: no cover
        """Подключиться и работать до отключения.

        `после_подключения` вызывается КОГДА КЛИЕНТ УЖЕ НА СВЯЗИ. Через него
        идёт добор сообщений, пришедших пока процесс лежал: до `start()` любой
        запрос к Telegram падает с «Cannot send requests while disconnected» —
        проверено живым прогоном, а не предположено.
        """
        await self.клиент.start()
        me = await self.клиент.get_me()
        print(f"вошли как: {me.first_name} (id {me.id})")
        if после_подключения is not None:
            try:
                await после_подключения()
            except Exception as e:
                # Сбой добора не имеет права мешать работе: клиенты, которые
                # напишут сейчас, важнее тех, кто написал ночью.
                print(f"добор пропущен: {type(e).__name__}: {e}")
        await self.клиент.run_until_disconnected()
