"""Imzochi bot: kanal imzosi + shaxsiy xabarlarga javob (/start)."""
import asyncio

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import sign_bot as sb

KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 Kanalimiz", url="https://t.me/Obloqulov_Tech")],
    [InlineKeyboardButton(text="💼 Bot buyurtma qilish", url="https://t.me/Obloqulov_v")],
])

TEXT = (
    "✍️ <b>Imzochi bot</b>\n\n"
    "Men kanal postlariga avtomatik imzo qo'yaman: har bir yangi postga muallif va kanal nomini qo'shaman.\n\n"
    "Hozircha faqat @Obloqulov_Tech kanalida ishlayman.\n\n"
    "O'z kanalingiz uchun shunday bot kerak bo'lsa, pastdagi tugmani bosing 👇"
)


@sb.dp.message(F.chat.type == "private")
async def on_private(m: Message):
    await m.answer(TEXT, reply_markup=KB, parse_mode="HTML")


async def main():
    me = await sb.bot.get_me()
    sb.log.info("Imzo bot ishga tushdi: @%s", me.username)
    await sb.bot.delete_webhook(drop_pending_updates=True)
    await sb.dp.start_polling(
        sb.bot,
        allowed_updates=["message", "channel_post", "edited_channel_post", "my_chat_member"])


if __name__ == "__main__":
    asyncio.run(main())
