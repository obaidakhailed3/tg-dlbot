#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بوت تحميل فيديو من انستغرام / فيسبوك / تيك توك — مع شرط الاشتراك بالقناة.

الإعداد عبر متغيرات البيئة (أو عدّل القيم بالأسفل مباشرة):
    BOT_TOKEN : توكن البوت من @BotFather
    CHANNEL   : يوزر القناة (مثال: @yalanchi) — لازم البوت يكون أدمن فيها

التشغيل:
    pip install -r requirements.txt
    BOT_TOKEN="123:ABC" CHANNEL="@mychannel" python bot.py
"""

import asyncio
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── الإعدادات ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
CHANNEL = os.environ.get("CHANNEL", "@PUT_CHANNEL_USERNAME")  # مثال: @yalanchi
# MODE: ‏"polling" للتلفون/اللابتوب، "webhook" للسيرفر (Render)
MODE = os.environ.get("MODE", "polling").lower()
MAX_MB = 45  # حد حجم الفيديو (تيليغرام يسمح حتى 50MB للبوتات)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
log = logging.getLogger("dlbot")

URL_RE = re.compile(r"https?://[^\s]+")
# روابط انستا / فيس / تيك توك فقط
ALLOWED_RE = re.compile(
    r"https?://([a-z0-9-]+\.)?(instagram\.com|facebook\.com|fb\.watch|tiktok\.com|vt\.tiktok\.com)/",
    re.IGNORECASE,
)

# حد أقصى: تحميلان بنفس الوقت (حتى لا يختنق التلفون)
sem = asyncio.Semaphore(2)


def channel_url() -> str:
    return "https://t.me/" + CHANNEL.lstrip("@")


def subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 اشترك بالقناة أولاً", url=channel_url())],
            [InlineKeyboardButton("✅ تم الاشتراك — تحقق", callback_data="check_sub")],
        ]
    )


async def is_subscribed(bot, user_id: int) -> bool:
    """يفحص عضوية المستخدم في القناة (البوت لازم أدمن بالقناة)."""
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("get_chat_member failed: %s", e)
        return False


async def require_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يرجع True إذا مشترك، وإلا يدز رسالة الاشتراك ويرجع False."""
    user = update.effective_user
    if await is_subscribed(context.bot, user.id):
        return True
    await update.message.reply_text(
        "👋 أهلاً بيك!\n\n"
        "📌 شرط استخدام البوت: الاشتراك بقناتنا أولاً.\n"
        "اشترك ثم اضغط «تم الاشتراك».",
        reply_markup=subscribe_keyboard(),
    )
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_subscription(update, context):
        return
    await update.message.reply_text(
        "✅ تمام، أنت مشترك!\n\n"
        "📥 دزلي رابط فيديو من:\n"
        "• انستغرام\n"
        "• فيسبوك\n"
        "• تيك توك\n\n"
        "وأنا أحمله وأدزه لك 🎬"
    )


async def check_sub_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر «تم الاشتراك» — يتحقق ويرحب."""
    query = update.callback_query
    await query.answer()
    if await is_subscribed(context.bot, query.from_user.id):
        await query.message.reply_text(
            "✅ تم التحقق — أهلاً بيك!\nدزلي رابط الفيديو 📥"
        )
    else:
        await query.message.reply_text(
            "❌ بعدك ما مشترك بالقناة.\nاشترك أولاً ثم اضغط الزر.",
            reply_markup=subscribe_keyboard(),
        )


def download_sync(url: str, outdir: str) -> Path | None:
    """يحمّل الفيديو بـ yt-dlp ويرجع مسار الملف أو None عند الفشل."""
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--max-filesize", f"{MAX_MB}M",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format", "mp4",
        "-o", os.path.join(outdir, "%(title).60s.%(ext)s"),
        url,
    ]
    # كوكيز انستغرام/فيسبوك (اختياري): ضع ملف cookies.txt بجانب البوت
    cookies = Path("cookies.txt")
    if cookies.exists():
        cmd[1:1] = ["--cookies", str(cookies)]

    log.info("downloading: %s", url)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        log.warning("yt-dlp failed: %s", proc.stderr[-500:])
        return None
    files = sorted(Path(outdir).glob("*"), key=lambda p: p.stat().st_size, reverse=True)
    return files[0] if files else None


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_subscription(update, context):
        return

    text = update.message.text or ""
    m = URL_RE.search(text)
    if not m:
        return
    url = m.group(0).rstrip(").,!?")

    if not ALLOWED_RE.search(url):
        await update.message.reply_text(
            "⚠️ البوت يدعم فقط روابط:\n• انستغرام\n• فيسبوك\n• تيك توك"
        )
        return

    status = await update.message.reply_text("⏳ جاري التحميل... ثواني ⏳")

    async with sem:
        loop = asyncio.get_running_loop()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = await loop.run_in_executor(None, download_sync, url, tmp)
                if path is None or not path.exists():
                    await status.edit_text(
                        "❌ ما كدرت أحمّل الفيديو.\n"
                        "يمكن الرابط خاص/محذوف، أو حجمه أكبر من 45MB."
                    )
                    return
                await status.edit_text("📤 جاري الإرسال...")
                with open(path, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        caption="🎬 تفضل — حمّلته لك\n📢 " + channel_url(),
                    )
        except Exception as e:
            log.exception("download error")
            await status.edit_text("❌ صار خطأ أثناء التحميل، حاول مرة ثانية.")
            return

    try:
        await status.delete()
    except Exception:
        pass


def main():
    if BOT_TOKEN == "PUT_YOUR_TOKEN_HERE" or CHANNEL == "@PUT_CHANNEL_USERNAME":
        raise SystemExit(
            "❌ لازم تحدد BOT_TOKEN و CHANNEL (متغيرات بيئة أو عدّل الملف مباشرة)."
        )
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(check_sub_button, pattern="^check_sub$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    if MODE == "webhook":
        # وضع السيرفر: تيليغرام يدز التحديثات للبوت (أوفر وأثبت)
        port = int(os.environ.get("PORT", "8443"))
        public_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_URL", "")
        if not public_url:
            raise SystemExit("❌ وضع webhook يحتاج RENDER_EXTERNAL_URL أو PUBLIC_URL.")
        webhook_url = f"{public_url}/{BOT_TOKEN}"
        log.info("webhook mode ✅ %s", webhook_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=webhook_url,
        )
    else:
        log.info("polling mode ✅")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
