"""
Imzo bot — kanalga tashlangan har bir post oxiriga imzo qo'shadi.

O'zgaruvchilar (Railway → Variables):
  SIGN_BOT_TOKEN   — BotFather bergan token (majburiy)
  OWNER            — sizning username'ingiz (standart @Obloqulov_v)
  SIGNATURE        — imzo shabloni; {owner} va {channel} o'rniga qiymatlar qo'yiladi.
                     Yangi qator uchun \\n yozing.
  ALLOWED_CHANNELS — faqat shu kanallarda ishlaydi, vergul bilan: @Obloqulov_Tech,@boshqa
                     (bo'sh bo'lsa — bot admin qilingan har qanday kanalda ishlaydi)

Bot kanalda admin bo'lishi va "Xabarlarni tahrirlash" ruxsatiga ega bo'lishi shart.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message, MessageEntity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("imzo")

TOKEN = os.environ["SIGN_BOT_TOKEN"]
OWNER = os.getenv("OWNER", "@Obloqulov_v").strip()
TEMPLATE = os.getenv("SIGNATURE", "👤 {owner}\\n📢 {channel}").replace("\\n", "\n")
ALLOWED = {c.strip().lower().lstrip("@") for c in os.getenv("ALLOWED_CHANNELS", "").split(",") if c.strip()}

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024

bot = Bot(TOKEN)  # parse_mode yo'q: formatlash entity'lar orqali saqlanadi
dp = Dispatcher()

albums: dict[str, list[Message]] = {}


def u16(s: str) -> int:
    """Telegram matn uzunligini UTF-16 birliklarda hisoblaydi."""
    return len(s.encode("utf-16-le")) // 2


def allowed(m: Message) -> bool:
    if not ALLOWED:
        return True
    uname = (m.chat.username or "").lower()
    return uname in ALLOWED or str(m.chat.id) in ALLOWED


def signature_for(m: Message) -> str:
    channel = f"@{m.chat.username}" if m.chat.username else (m.chat.title or "")
    return TEMPLATE.format(owner=OWNER, channel=channel)


def already_signed(body: str, sig: str) -> bool:
    return body.rstrip().endswith(sig.strip()) or OWNER.lower() in body.lower()[-200:]


async def sign(m: Message, force_caption: bool = False):
    """Postga imzo qo'shadi. Matn bo'lsa — matnni, media bo'lsa — izohni tahrirlaydi."""
    if not allowed(m):
        return
    sig = signature_for(m)

    if m.text is not None:
        body, entities, limit, is_text = m.text, list(m.entities or []), TEXT_LIMIT, True
    elif m.caption is not None or force_caption or m.photo or m.video or m.document \
            or m.animation or m.audio or m.voice:
        body, entities, limit, is_text = m.caption or "", list(m.caption_entities or []), CAPTION_LIMIT, False
    else:
        return  # stiker, so'rovnoma va h.k. — tahrirlab bo'lmaydi

    if body and already_signed(body, sig):
        return

    new_text = (body.rstrip() + "\n\n" + sig) if body.strip() else sig
    if u16(new_text) > limit:
        log.info("Post juda uzun, imzo sig'madi: chat=%s msg=%s", m.chat.id, m.message_id)
        return

    # oxirgi bo'sh joylar kesilgan bo'lsa, entity chegaralarini moslaymiz
    cut = u16(body.rstrip())
    fixed = []
    for e in entities:
        if e.offset >= cut:
            continue
        end = min(e.offset + e.length, cut)
        fixed.append(MessageEntity(**{**e.model_dump(exclude_none=True), "length": end - e.offset}))

    for attempt in range(2):
        try:
            if is_text:
                await bot.edit_message_text(
                    text=new_text, chat_id=m.chat.id, message_id=m.message_id,
                    entities=fixed or None, parse_mode=None,
                    link_preview_options=m.link_preview_options,
                    reply_markup=m.reply_markup)
            else:
                await bot.edit_message_caption(
                    caption=new_text, chat_id=m.chat.id, message_id=m.message_id,
                    caption_entities=fixed or None, parse_mode=None,
                    show_caption_above_media=m.show_caption_above_media,
                    reply_markup=m.reply_markup)
            log.info("Imzo qo'shildi: %s #%s", m.chat.username or m.chat.id, m.message_id)
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "not modified" not in msg:
                log.warning("Tahrirlab bo'lmadi (%s #%s): %s", m.chat.id, m.message_id, e)
            return


async def flush_album(gid: str):
    """Albom: izohi bor elementga imzo qo'yiladi; izoh yo'q bo'lsa — birinchi elementga."""
    await asyncio.sleep(2.5)
    items = sorted(albums.pop(gid, []), key=lambda x: x.message_id)
    if not items:
        return
    target = next((x for x in items if x.caption), items[0])
    await sign(target, force_caption=True)


@dp.channel_post()
async def on_post(m: Message):
    if m.media_group_id:
        first = m.media_group_id not in albums
        albums.setdefault(m.media_group_id, []).append(m)
        if first:
            asyncio.create_task(flush_album(m.media_group_id))
        return
    await sign(m)


@dp.edited_channel_post()
async def on_edit(m: Message):
    # admin postni tahrirlab imzoni o'chirib yuborsa — qayta qo'shiladi
    if m.media_group_id and not m.caption:
        return
    await sign(m)


@dp.my_chat_member()
async def on_added(update):
    chat = update.chat
    status = update.new_chat_member.status
    log.info("Bot holati o'zgardi: %s (%s) -> %s", chat.title, chat.username, status)
    if status == "administrator" and ALLOWED:
        uname = (chat.username or "").lower()
        if uname not in ALLOWED and str(chat.id) not in ALLOWED:
            log.info("Ruxsat etilmagan kanal, chiqib ketilmoqda: %s", chat.username)
            await bot.leave_chat(chat.id)


async def main():
    me = await bot.get_me()
    log.info("Imzo bot ishga tushdi: @%s | imzo: %r", me.username, TEMPLATE)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["channel_post", "edited_channel_post", "my_chat_member"])


if __name__ == "__main__":
    asyncio.run(main())
