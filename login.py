"""Разовый вход в аккаунт. Запускается руками, печатает строку сессии.

Отдельно от main.py намеренно: рабочий процесс не должен уметь спрашивать
код подтверждения. Подтверждает человек.

    python login.py

Полученную строку положить в .env как TG_SESSION. Это полноценный доступ
к аккаунту — .env в git не попадает.

ДВА ПУТИ ВХОДА, и по умолчанию QR. Причина — в документации Telegram
(core.telegram.org/api/auth), а не в удобстве:

  «only the official applications can receive a login/signup code via SMS/call»
  «Third-party apps … may log in using any of the other code delivery methods
   (Telegram codes, Fragment codes, email codes, future auth tokens, QR codes…)»

То есть SMS нам недоступна в принципе: мы сторонний клиент. Остаётся код
внутрь приложения — а он, по той же документации, уходит «as a Telegram
service notification to all other logged-in sessions», то есть требует уже
живой сессии и умеет не находить адресата. Живой случай 2026-08-13: код не
пришёл, ошибки нет, next_type пустой. QR такой зависимости не имеет.

Почему не client.start(): он выбрасывает ответ Telegram о способе доставки
кода, а повторный запрос тратит лимит номера — диагноз нужен с первой попытки.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from getpass import getpass

import qrcode
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.auth import ResendCodeRequest

from aiconic import env


def _куда_ушёл_код(тип) -> list[str]:
    """Человеческим языком: где именно искать код."""
    имя = type(тип).__name__
    if имя == "SentCodeTypeApp":
        return [
            "Код ВНУТРИ Telegram: чат «Telegram» (синяя галочка), в аккаунте салона.",
            "SMS не будет — у аккаунта есть активная сессия, Telegram шлёт код в неё.",
        ]
    if имя == "SentCodeTypeSms":
        return ["Код в SMS на этот номер."]
    if имя == "SentCodeTypeFragmentSms":
        return [
            "Код НЕ придёт на телефон: Telegram отдал его Fragment.",
            f"Забирать здесь: {getattr(тип, 'url', '(ссылку не дали)')}",
            "Так бывает с виртуальными и перепроданными номерами.",
        ]
    if имя == "SentCodeTypeMissedCall":
        return [
            "Будет СБРОШЕННЫЙ ЗВОНОК. Код — последние цифры номера звонящего.",
            f"Номер начнётся на {getattr(тип, 'prefix', '')}.",
        ]
    if имя == "SentCodeTypeCall":
        return ["Робот ПОЗВОНИТ и продиктует код голосом."]
    if имя == "SentCodeTypeFlashCall":
        return [
            "Будет короткий звонок, код — часть номера звонящего.",
            f"Шаблон: {getattr(тип, 'pattern', '')}",
        ]
    if имя in ("SentCodeTypeSmsWord", "SentCodeTypeSmsPhrase"):
        return [
            "Код придёт СЛОВОМ в SMS, а не цифрами.",
            f"Начинается на: {getattr(тип, 'beginning', '')}",
        ]
    if имя == "SentCodeTypeEmailCode":
        return [f"Код на почту {getattr(тип, 'email_pattern', '')}."]
    if имя == "SentCodeTypeSetUpEmailRequired":
        return [
            "Кода не будет: Telegram требует сначала привязать к аккаунту почту.",
            "Сделайте это в официальном приложении, потом входите здесь по QR.",
        ]
    if имя == "SentCodeTypeFirebaseSms":
        return [
            "SMS через Firebase. Официальное приложение подтверждает такой код",
            "само, при входе по API он может не прийти вовсе.",
        ]
    return [f"Неизвестный способ доставки: {имя}. Скажите мне это название."]


def _показать(отправлено) -> None:
    тип = getattr(отправлено, "type", None)
    if тип is None:
        print(f"Telegram ответил необычно: {type(отправлено).__name__}")
        print("Скажите мне это название — разберём.")
        return

    print()
    print("--- куда Telegram отправил код ---")
    for строка in _куда_ушёл_код(тип):
        print(f"  {строка}")
    длина = getattr(тип, "length", None)
    if длина:
        print(f"  Длина кода: {длина} символов.")

    следующий = getattr(отправлено, "next_type", None)
    if следующий is not None:
        print(f"  Если не дойдёт — «ещё» попросит: {type(следующий).__name__}")
    else:
        print("  Другого способа Telegram не предлагает.")
    таймаут = getattr(отправлено, "timeout", None)
    if таймаут:
        print(f"  Повторный запрос доступен через {таймаут} с.")
    print("-" * 34)
    print()


def _нарисовать_qr(ссылка: str) -> None:
    буфер = io.StringIO()
    код = qrcode.QRCode(border=1)
    код.add_data(ссылка)
    код.print_ascii(out=буфер, invert=True)
    print()
    print(буфер.getvalue().rstrip())
    print()
    print("На телефоне, в аккаунте САЛОНА:")
    print("  Настройки → Устройства → Подключить устройство → наведите на QR")
    print()
    print("Если камера не берёт с экрана — откройте на телефоне эту ссылку:")
    print(f"  {ссылка}")
    print()
    print("Жду подтверждения…")


async def _вход_по_qr(клиент) -> None:
    """QR не зависит от доставки кода: подтверждает уже живая сессия."""
    вход = await клиент.qr_login()
    while True:
        _нарисовать_qr(вход.url)
        try:
            await вход.wait()
            return
        except asyncio.TimeoutError:
            print("QR истёк, рисую новый.")
            await вход.recreate()
        except SessionPasswordNeededError:
            пароль = getpass("Пароль двухфакторной защиты (не отображается): ")
            await клиент.sign_in(password=пароль)
            return


async def _вход_по_коду(клиент) -> int:
    номер = input("Номер аккаунта салона, например +995555123456: ").strip()
    if not номер.startswith("+"):
        print("Номер нужен с плюсом и кодом страны.")
        return 1

    отправлено = await клиент.send_code_request(номер)
    _показать(отправлено)

    while True:
        код = input("Код (или «ещё» — другой способ, «выход» — прервать): ").strip()
        if код in ("выход", "exit", ""):
            print("Прервано. Запустите заново, когда будет код.")
            return 1
        if код in ("ещё", "еще", "resend"):
            отправлено = await клиент(
                ResendCodeRequest(номер, отправлено.phone_code_hash)
            )
            _показать(отправлено)
            continue
        try:
            await клиент.sign_in(номер, код)
            return 0
        except PhoneCodeInvalidError:
            print("Код неверный. Проверьте и введите снова.")
        except PhoneCodeExpiredError:
            print("Код истёк. Введите «ещё», чтобы запросить новый.")
        except SessionPasswordNeededError:
            пароль = getpass("Пароль двухфакторной защиты (не отображается): ")
            await клиент.sign_in(password=пароль)
            return 0


async def main() -> int:
    env.загрузить()

    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")

    if not api_id or not api_hash:
        print("Сначала задайте TG_API_ID и TG_API_HASH (см. .env.example).")
        print("Получить их: https://my.telegram.org → API development tools.")
        print("Важно: один api_id на один номер.")
        return 1

    print("Как войти:")
    print("  1 — по QR (рекомендую: не зависит от доставки кода)")
    print("  2 — по коду из Telegram")
    выбор = input("1 или 2 [1]: ").strip() or "1"

    клиент = TelegramClient(StringSession(), int(api_id), api_hash)
    await клиент.connect()
    try:
        if выбор == "2":
            если_не_вышло = await _вход_по_коду(клиент)
            if если_не_вышло:
                return если_не_вышло
        else:
            await _вход_по_qr(клиент)

        я = await клиент.get_me()
        строка = клиент.session.save()
        print()
        print(f"Вошли как: {я.first_name} (id {я.id}) — это аккаунт САЛОНА.")
        print()
        print("⚠️ Этот id в TG_WHITELIST НЕ нужен. Туда идёт id того, КТО ПИШЕТ")
        print("   салону, то есть вашего личного аккаунта. Возьмите его из")
        print("   logs/dialogs.jsonl после первого запуска в режиме наблюдения.")
        print()
        print("Положите это в .env как TG_SESSION:")
        print()
        print(строка)
        print()
        print("Это ПОЛНЫЙ доступ к аккаунту. Никому не показывайте, в git не кладите.")
        print("Дальше: напишите @SpamBot и проверьте, нет ли ограничений на аккаунте.")
    finally:
        await клиент.disconnect()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nпрервано")
