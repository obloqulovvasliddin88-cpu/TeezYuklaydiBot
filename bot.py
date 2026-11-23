"""
Video yuklovchi Telegram bot — TikTok, Instagram, YouTube.

O'zgaruvchilar (Railway → Variables):
  BOT_TOKEN    — BotFather bergan token (majburiy)
  ADMIN_IDS    — admin Telegram ID lari, vergul bilan: 123456,789012
  CHANNEL      — majburiy obuna kanali, masalan @kings_perfume (ixtiyoriy)
  COOKIES_TXT  — cookies.txt matni (Instagram/YouTube bloklasa, ixtiyoriy)
  MAX_MB       — Telegramga yuboriladigan eng katta fayl, MB (standart 49)
  PARALLEL     — bir vaqtda nechta yuklash (standart 3)
  DB_PATH      — baza fayli (standart data/bot.db)
"""
import asyncio
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from urllib.parse import parse_qs, urlparse

import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("yuklovchi")

# ---------------- sozlamalar ----------------
TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
CHANNEL = os.getenv("CHANNEL", "").strip()
MAX_MB = int(os.getenv("MAX_MB", "49"))
PARALLEL = int(os.getenv("PARALLEL", "3"))
DB_PATH = os.getenv("DB_PATH", "data/bot.db")

COOKIES_PATH = ""
if os.getenv("COOKIES_TXT"):
    COOKIES_PATH = os.path.join(tempfile.gettempdir(), "cookies.txt")
    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        f.write(os.environ["COOKIES_TXT"])

URL_RE = re.compile(r"https?://\S+")
PLATFORMS = {
    "tiktok.com": "TikTok",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
}

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
sem = asyncio.Semaphore(PARALLEL)
busy_users: set[int] = set()
pending_links: dict[str, str] = {}  # qisqa id -> havola (tugmalar uchun)
BOT_USERNAME = ""

# ---------------- baza ----------------
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, joined INTEGER);
CREATE TABLE IF NOT EXISTS downloads(user_id INTEGER, platform TEXT, mode TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS cache(key TEXT, mode TEXT, file_id TEXT, PRIMARY KEY(key, mode));
""")
db.commit()


def save_user(m: Message):
    db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?)",
               (m.from_user.id, m.from_user.full_name, int(time.time())))
    db.commit()


def log_download(uid, platform, mode):
    db.execute("INSERT INTO downloads VALUES(?,?,?,?)", (uid, platform, mode, int(time.time())))
    db.commit()


def cache_get(key, mode):
    r = db.execute("SELECT file_id FROM cache WHERE key=? AND mode=?", (key, mode)).fetchone()
    return r[0] if r else None


def cache_put(key, mode, file_id):
    db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, mode, file_id))
    db.commit()


# ---------------- yordamchilar ----------------
def platform_of(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    for dom, name in PLATFORMS.items():
        if host == dom or host.endswith("." + dom):
            return name
    return None


def url_key(url: str) -> str:
    """Bir xil videoni qayta yuklamaslik uchun havolani tozalaydi."""
    p = urlparse(url)
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    q = parse_qs(p.query)
    keep = f"?v={q['v'][0]}" if "v" in q else ""
    return host + p.path.rstrip("/") + keep


def caption() -> str:
    return f"📥 @{BOT_USERNAME} orqali yuklandi" if BOT_USERNAME else ""


def mp3_button(link_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎵 MP3 qilib olish", callback_data=f"mp3:{link_id}")
    ]])


async def is_subscribed(uid: int) -> bool:
    if not CHANNEL or uid in ADMIN_IDS:
        return True
    try:
        mem = await bot.get_chat_member(CHANNEL, uid)
        return mem.status in ("member", "administrator", "creator")
    except Exception as e:  # bot kanalda admin bo'lmasa — obunani tekshirmaymiz
        log.warning("Obunani tekshirib bo'lmadi: %s", e)
        return True


async def ask_subscribe(m: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Kanalga obuna bo'lish",
                              url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")],
    ])
    await m.answer("Botdan foydalanish uchun kanalimizga obuna bo'ling 👇", reply_markup=kb)


# ---------------- yuklash ----------------
class TooBig(Exception):
    pass


def _download(url: str, mode: str, workdir: str):
    opts = {
        "outtmpl": os.path.join(workdir, "%(id)s_%(autonumber)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": MAX_MB * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 3,
        "restrictfilenames": True,
    }
    if COOKIES_PATH:
        opts["cookiefile"] = COOKIES_PATH
    if mode == "video":
        opts["format"] = (f"best[ext=mp4][filesize<{MAX_MB}M]/"
                          f"bv*[ext=mp4][height<=720]+ba[ext=m4a]/"
                          f"b[height<=720]/b")
        opts["merge_output_format"] = "mp4"
    else:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                   "preferredcodec": "mp3", "preferredquality": "192"}]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}
    files = sorted(
        os.path.join(workdir, f) for f in os.listdir(workdir)
        if not f.endswith((".part", ".ytdl", ".jpg", ".webp", ".png"))
    )
    if not files:
        raise TooBig()
    files = [f for f in files if os.path.getsize(f) <= MAX_MB * 1024 * 1024]
    if not files:
        raise TooBig()
    if "entries" in info and info["entries"]:
        info = info["entries"][0] or {}
    return files[:10], info


def friendly_error(e: Exception) -> str:
    s = str(e).lower()
    if isinstance(e, TooBig):
        return f"😔 Video juda katta (Telegram limiti {MAX_MB} MB). Qisqaroq video yuboring."
    if "private" in s or "login" in s or "sign in" in s or "cookies" in s:
        return "🔒 Bu video yopiq yoki uni ko'rish uchun akkauntga kirish kerak."
    if "unsupported url" in s:
        return "❌ Bu havolani tanimadim. TikTok, Instagram yoki YouTube havolasini yuboring."
    if "not available" in s or "removed" in s:
        return "❌ Video o'chirilgan yoki mavjud emas."
    return "😔 Yuklab bo'lmadi. Birozdan keyin qayta urinib ko'ring."


async def process(chat_id: int, uid: int, url: str, mode: str, link_id: str):
    platform = platform_of(url) or "Boshqa"
    key = url_key(url)
    send_kw = {"caption": caption()}
    if mode == "video":
        send_kw["reply_markup"] = mp3_button(link_id)

    cached = cache_get(key, mode)
    if cached:
        try:
            if mode == "video":
                await bot.send_video(chat_id, cached, **send_kw)
            else:
                await bot.send_audio(chat_id, cached, **send_kw)
            log_download(uid, platform, mode)
            return
        except TelegramBadRequest:
            pass  # file_id eskirgan bo'lsa, qayta yuklaymiz

    status = await bot.send_message(chat_id, "⏳ Yuklanmoqda...")
    workdir = tempfile.mkdtemp(prefix="dl_")
    try:
        async with sem:
            await bot.send_chat_action(
                chat_id, ChatAction.UPLOAD_VIDEO if mode == "video" else ChatAction.UPLOAD_VOICE)
            files, info = await asyncio.to_thread(_download, url, mode, workdir)

        for i, path in enumerate(files):
            kw = send_kw if i == len(files) - 1 else {}
            if mode == "video":
                msg = await bot.send_video(
                    chat_id, FSInputFile(path), supports_streaming=True,
                    width=info.get("width"), height=info.get("height"),
                    duration=int(info["duration"]) if info.get("duration") else None, **kw)
                file_id = msg.video.file_id if msg.video else None
            else:
                msg = await bot.send_audio(
                    chat_id, FSInputFile(path),
                    title=(info.get("title") or "audio")[:64],
                    performer=(info.get("uploader") or "")[:64] or None, **kw)
                file_id = msg.audio.file_id if msg.audio else None
            if len(files) == 1 and file_id:
                cache_put(key, mode, file_id)
        log_download(uid, platform, mode)
        await status.delete()
    except Exception as e:
        log.warning("Xato %s: %s", url, e)
        try:
            await status.edit_text(friendly_error(e))
        except TelegramBadRequest:
            pass
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def start_job(chat_id: int, uid: int, url: str, mode: str, link_id: str):
    if uid in busy_users:
        await bot.send_message(chat_id, "⏳ Oldingi yuklash tugashini kuting.")
        return
    busy_users.add(uid)
    try:
        await process(chat_id, uid, url, mode, link_id)
    finally:
        busy_users.discard(uid)


# ---------------- buyruqlar ----------------
@dp.message(CommandStart())
async def cmd_start(m: Message):
    save_user(m)
    await m.answer(
        f"Salom, {m.from_user.first_name}! 👋\n\n"
        "Menga <b>TikTok</b>, <b>Instagram</b> yoki <b>YouTube</b> video havolasini yuboring — "
        "videoni suv belgisiz yuklab beraman.\n\n"
        "🎵 Video ostidagi tugma orqali MP3 ham olishingiz mumkin."
    )


@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer("Video havolasini nusxalab, shu yerga yuboring. Masalan:\n"
                   "https://www.tiktok.com/@user/video/...\n"
                   "https://www.instagram.com/reel/...\n"
                   "https://youtube.com/shorts/...")


@dp.message(Command("stat"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_stat(m: Message):
    day = int(time.time()) - 86400
    users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    new = db.execute("SELECT COUNT(*) FROM users WHERE joined>?", (day,)).fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
    today = db.execute("SELECT COUNT(*) FROM downloads WHERE ts>?", (day,)).fetchone()[0]
    rows = db.execute("SELECT platform, COUNT(*) FROM downloads WHERE ts>? GROUP BY platform",
                      (day,)).fetchall()
    by = "\n".join(f"  • {p}: {c}" for p, c in rows) or "  —"
    await m.answer(f"📊 <b>Statistika</b>\n\n"
                   f"👥 Foydalanuvchilar: {users} (24 soatda +{new})\n"
                   f"📥 Yuklashlar: {total} (24 soatda {today})\n{by}")


@dp.message(Command("xabar"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_broadcast(m: Message):
    """Istalgan xabarga javob (reply) qilib /xabar yozing — hammaga yuboriladi."""
    src = m.reply_to_message
    if not src:
        await m.answer("Tarqatmoqchi bo'lgan xabarga <b>reply</b> qilib /xabar yozing.")
        return
    ids = [r[0] for r in db.execute("SELECT id FROM users").fetchall()]
    info = await m.answer(f"📤 {len(ids)} kishiga yuborilmoqda...")
    ok = fail = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, src.chat.id, src.message_id)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(uid, src.chat.id, src.message_id)
                ok += 1
            except Exception:
                fail += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            fail += 1
        await asyncio.sleep(0.05)
    await info.edit_text(f"✅ Yuborildi: {ok}\n❌ Yetmadi (botni bloklagan): {fail}")


# ---------------- havolalar ----------------
@dp.message(F.chat.type == "private", F.text.regexp(URL_RE))
async def on_link(m: Message):
    save_user(m)
    if not await is_subscribed(m.from_user.id):
        await ask_subscribe(m)
        return
    url = URL_RE.search(m.text).group(0).rstrip(").,")
    if not platform_of(url):
        await m.answer("❌ Hozircha faqat TikTok, Instagram va YouTube havolalarini yuklayman.")
        return
    link_id = uuid.uuid4().hex[:12]
    pending_links[link_id] = url
    if len(pending_links) > 5000:  # xotira to'lib ketmasin
        for k in list(pending_links)[:1000]:
            pending_links.pop(k, None)
    await start_job(m.chat.id, m.from_user.id, url, "video", link_id)


@dp.callback_query(F.data.startswith("mp3:"))
async def on_mp3(c: CallbackQuery):
    link_id = c.data.split(":", 1)[1]
    url = pending_links.get(link_id)
    if not url:
        await c.answer("Havola eskirgan, uni qayta yuboring.", show_alert=True)
        return
    await c.answer("🎵 MP3 tayyorlanmoqda...")
    await start_job(c.message.chat.id, c.from_user.id, url, "audio", link_id)


@dp.callback_query(F.data == "check_sub")
async def on_check_sub(c: CallbackQuery):
    if await is_subscribed(c.from_user.id):
        await c.message.edit_text("✅ Rahmat! Endi video havolasini yuboring.")
    else:
        await c.answer("Hali obuna bo'lmadingiz 🙂", show_alert=True)


@dp.message(F.chat.type == "private")
async def on_other(m: Message):
    save_user(m)
    await m.answer("Menga TikTok, Instagram yoki YouTube video havolasini yuboring 🔗")


async def main():
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username
    log.info("Bot ishga tushdi: @%s", BOT_USERNAME)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
