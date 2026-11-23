"""Imzochi bot + egasi uchun boshqaruv paneli."""
import asyncio, html, json, logging, os
from datetime import date, timedelta
from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup as KM, Message
import sign_bot as sb

ADMIN = int(os.getenv("ADMIN_ID", "0"))
PATH = os.getenv("STATE_PATH", "data/state.json")
os.makedirs(os.path.dirname(PATH), exist_ok=True)
S = {"signature": sb.TEMPLATE, "paused": False, "total": 0, "days": {}}
try:
    S.update(json.load(open(PATH, encoding="utf-8")))
except Exception:
    pass
sb.TEMPLATE = S["signature"]
wait = set()


def save():
    json.dump(S, open(PATH + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(PATH + ".tmp", PATH)


_sign = sb.sign
async def guarded(m, force_caption=False):
    if not S["paused"]:
        await _sign(m, force_caption=force_caption)
sb.sign = guarded


def notify(text):
    try:
        asyncio.get_running_loop().create_task(sb.bot.send_message(ADMIN, text))
    except Exception:
        pass


class Hook(logging.Handler):
    def emit(self, r):
        t = r.getMessage()
        if t.startswith("Imzo qo'shildi"):
            d = date.today().isoformat()
            S["total"] += 1
            S["days"][d] = S["days"].get(d, 0) + 1
            for k in sorted(S["days"])[:-60]:
                S["days"].pop(k)
            save()
        elif ADMIN and t.startswith(("Bot holati", "Ruxsat etilmagan", "Tahrirlab", "Post juda")):
            notify("🔔 " + t)
sb.log.addHandler(Hook())

CH = "@" + (os.getenv("ALLOWED_CHANNELS", "").split(",")[0].strip().lstrip("@") or "kanal")
PUBLIC = ("✍️ <b>Imzochi bot</b>\n\nMen kanal postlariga avtomatik imzo qo'yaman: har bir yangi "
          f"postga muallif va kanal nomini qo'shaman.\n\nHozircha faqat {CH} kanalida ishlayman.\n\n"
          "O'z kanalingiz uchun shunday bot kerak bo'lsa, pastdagi tugmani bosing 👇")
PUB_KB = KM(inline_keyboard=[[B(text="📢 Kanalimiz", url=f"https://t.me/{CH[1:]}")],
                             [B(text="💼 Bot buyurtma qilish", url=f"https://t.me/{sb.OWNER.lstrip('@')}")]])


def preview(tpl=None):
    return (tpl or sb.TEMPLATE).format(owner=sb.OWNER, channel=CH)


def panel():
    st = "⏸ To'xtatilgan" if S["paused"] else "✅ Ishlayapti"
    txt = (f"👑 <b>Boshqaruv paneli</b>\n\nHolat: {st}\nKanal: {CH}\n\n"
           f"Joriy imzo:\n<blockquote>{html.escape(preview())}</blockquote>")
    kb = KM(inline_keyboard=[
        [B(text="📊 Statistika", callback_data="a:stat")],
        [B(text="✏️ Imzoni o'zgartirish", callback_data="a:edit")],
        [B(text="▶️ Yoqish" if S["paused"] else "⏸ To'xtatish", callback_data="a:tog")]])
    return txt, kb


is_admin = F.from_user.id == ADMIN
private = F.chat.type == "private"


@sb.dp.message(private, is_admin, Command("bekor"))
async def cancel(m: Message):
    wait.discard(m.from_user.id)
    t, k = panel()
    await m.answer("Bekor qilindi.\n\n" + t, reply_markup=k, parse_mode="HTML")


@sb.dp.message(private, is_admin)
async def admin_msg(m: Message):
    if m.from_user.id in wait and m.text and not m.text.startswith("/"):
        tpl = m.text.strip()
        try:
            p = preview(tpl)
        except Exception:
            await m.answer("❌ Imzoda xato bor. Faqat {owner} va {channel} belgilarini ishlating.")
            return
        if len(tpl) > 300:
            await m.answer("❌ Imzo juda uzun (300 belgigacha bo'lsin).")
            return
        sb.TEMPLATE = S["signature"] = tpl
        save()
        wait.discard(m.from_user.id)
        t, k = panel()
        await m.answer(f"✅ Yangi imzo saqlandi:\n<blockquote>{html.escape(p)}</blockquote>\n\n" + t,
                       reply_markup=k, parse_mode="HTML")
        return
    t, k = panel()
    await m.answer(t, reply_markup=k, parse_mode="HTML")


@sb.dp.message(private)
async def public_msg(m: Message):
    await m.answer(PUBLIC, reply_markup=PUB_KB, parse_mode="HTML")


@sb.dp.callback_query(F.data.startswith("a:"), is_admin)
async def admin_cb(c: CallbackQuery):
    act = c.data[2:]
    if act == "tog":
        S["paused"] = not S["paused"]
        save()
        await c.answer("⏸ Imzo to'xtatildi" if S["paused"] else "▶️ Imzo yoqildi")
    elif act == "edit":
        wait.add(c.from_user.id)
        await c.answer()
        await c.message.answer(
            "✏️ Yangi imzo matnini yuboring.\n\n<code>{owner}</code> o'rniga sizning username'ingiz, "
            "<code>{channel}</code> o'rniga kanal nomi qo'yiladi.\n\nMasalan:\n"
            "<code>👤 Admin: {owner}\n📢 Obuna bo'ling: {channel}</code>\n\nBekor qilish: /bekor",
            parse_mode="HTML")
        return
    elif act == "stat":
        today = date.today()
        d = S["days"]
        week = sum(d.get((today - timedelta(days=i)).isoformat(), 0) for i in range(7))
        await c.answer()
        await c.message.answer(
            f"📊 <b>Statistika</b>\n\nBugun: {d.get(today.isoformat(), 0)} ta post\n"
            f"Oxirgi 7 kun: {week} ta post\nJami: {S['total']} ta post", parse_mode="HTML")
        return
    t, k = panel()
    try:
        await c.message.edit_text(t, reply_markup=k, parse_mode="HTML")
    except Exception:
        pass


async def main():
    me = await sb.bot.get_me()
    sb.log.info("Imzochi (panel bilan) ishga tushdi: @%s, admin=%s", me.username, ADMIN)
    await sb.bot.delete_webhook(drop_pending_updates=True)
    await sb.dp.start_polling(sb.bot, allowed_updates=[
        "message", "callback_query", "channel_post", "edited_channel_post", "my_chat_member"])


if __name__ == "__main__":
    asyncio.run(main())
