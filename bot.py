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
import math
import os
import re
import shutil
import subprocess
import tempfile
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
MAX_MB = 48  # حد حجم الفيديو (تيليغرام يسمح حتى 50MB للبوتات)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
log = logging.getLogger("dlbot")

URL_RE = re.compile(r"https?://[^\s]+")
# روابط المنصات المدعومة (نفس منصات التطبيق)
ALLOWED_RE = re.compile(
    r"https?://([a-z0-9-]+\.)?(instagram\.com|facebook\.com|fb\.watch|tiktok\.com|vt\.tiktok\.com"
    r"|youtube\.com|youtu\.be|twitter\.com|x\.com|snapchat\.com"
    r"|pinterest\.com|pin\.it|reddit\.com|dailymotion\.com|dai\.ly|vimeo\.com)/",
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
        "• تيك توك\n"
        "• يوتيوب\n"
        "• سناب شات\n"
        "• بنترست\n"
        "• تويتر\n"
        "• ريدت\n\n"
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


def count_cookies(path: Path) -> int:
    """يعدّ سطور الكوكيز الصالحة (7 حقول مفصولة بتاب) للتشخيص."""
    try:
        n = 0
        for line in path.read_text().splitlines():
            if line and not line.startswith("# ") and line.count("\t") == 6:
                n += 1
        return n
    except Exception:
        return 0


def find_cookies() -> Path | None:
    """يلكّي ملف الكوكيز (محلياً أو Secret File على Render) إن وجد وكان صالحاً."""
    # - محلياً: ملف cookies.txt بجانب البوت
    # - على Render: Secret File باسم cookies.txt (يُركّب تحت /etc/secrets/)
    c = Path("cookies.txt")
    if not c.exists():
        _sf = Path("/etc/secrets/cookies.txt")
        if _sf.exists():
            c = _sf
    if not c.exists():
        log.info("no cookies file found")
        return None
    n = count_cookies(c)
    log.info("cookies file: %s (%d valid cookies)", c, n)
    if n == 0:
        log.warning("cookies file has 0 valid lines — check tabs/paste format")
        return None
    return c


def download_tiktok_api(url: str, outdir: str) -> Path | None:
    """احتياطي تيك توك: عند حجب IP السيرفر من تيك توك، نحمّل عبر tikwm API
    (سيرفراتهم غير محجوبة) — يرجع مسار MP4 بدون علامة مائية."""
    import urllib.request
    import urllib.parse
    import json

    try:
        api = "https://www.tikwm.com/api/?url=" + urllib.parse.quote(url, safe="")
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        if data.get("code") != 0:
            log.warning("tiktok api error: %s", data.get("msg"))
            return None
        play = (data.get("data") or {}).get("play")
        if not play:
            return None
        out = Path(outdir) / "tiktok_api.mp4"
        req2 = urllib.request.Request(
            play, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tikwm.com/"}
        )
        with urllib.request.urlopen(req2, timeout=180) as r, open(out, "wb") as f:
            shutil.copyfileobj(r, f)
        if out.stat().st_size > 0:
            log.info("tiktok api fallback OK (%d bytes)", out.stat().st_size)
            return out
        return None
    except Exception as e:
        log.warning("tiktok api fallback failed: %s", e)
        return None


def download_sync(url: str, outdir: str) -> Path | None:
    """يحمّل الفيديو بـ yt-dlp ويرجع مسار الملف أو None عند الفشل."""
    is_youtube = "youtube.com" in url or "youtu.be" in url
    if is_youtube:
        # يوتيوب: المحاولة 1 = عميل أندرويد (سريع، بدون كوكيز)
        #         المحاولة 2 = العميل الافتراضي + الكوكيز (deno يحل تحديات الجافاسكربت)
        plans = [
            {"args": ["--extractor-args", "youtube:player_client=android"], "cookies": False},
            {"args": [], "cookies": True},
        ]
    else:
        plans = [{"args": [], "cookies": True}]

    cookies = find_cookies()
    for i, plan in enumerate(plans):
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--max-filesize", "1000M",  # سقف أمان للتحميل؛ حد الإرسال يُعالَج بالتقسيم
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "--merge-output-format", "mp4",
            "-o", os.path.join(outdir, "%(title).60s.%(ext)s"),
        ] + plan["args"]
        if plan["cookies"] and cookies:
            cmd += ["--cookies", str(cookies)]
        cmd.append(url)

        log.info("downloading (attempt %d/%d): %s", i + 1, len(plans), url)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0:
            files = sorted(Path(outdir).glob("*"), key=lambda p: p.stat().st_size, reverse=True)
            if files:
                return files[0]
        log.warning("yt-dlp attempt %d failed: %s", i + 1, proc.stderr[:800])
    # احتياطي تيك توك: إذا فشل yt-dlp (حجب IP السيرفر)، جرّب API خارجي
    if "tiktok.com" in url:
        log.info("trying tiktok api fallback for: %s", url)
        return download_tiktok_api(url, outdir)
    return None


def split_video(src: Path, tmp: Path, max_mb: int) -> list[Path]:
    """يقسّم الفيديو لأجزاء (كل جزء أقل من max_mb) بنسخ الستريم — سريع بدون إعادة ترميز."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True, timeout=60,
    )
    duration = float(probe.stdout.strip())
    # هدف 40MB للجزء (هامش أمان تحت حد تليغرام 50MB)
    n = max(2, math.ceil(src.stat().st_size / (40 * 1024 * 1024)))
    part_dur = duration / n
    out_pat = str(tmp / "part%02d.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-c", "copy", "-map", "0",
         "-f", "segment", "-segment_time", f"{part_dur:.2f}",
         "-reset_timestamps", "1", out_pat],
        capture_output=True, timeout=900, check=True,
    )
    parts = sorted(tmp.glob("part*.mp4"))
    # تحقق: أي جزء ما زال أكبر من الحد يُستبعد (نادر — تقسيم الـkeyframes)
    ok = [p for p in parts if p.stat().st_size <= max_mb * 1024 * 1024]
    return ok if len(ok) == len(parts) and ok else []


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
            "⚠️ البوت يدعم روابط: انستغرام، فيسبوك، تيك توك، يوتيوب، سناب شات، بنترست، تويتر، ريدت"
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
                        "يمكن الرابط خاص/محذوف."
                    )
                    return
                limit = MAX_MB * 1024 * 1024
                if path.stat().st_size <= limit:
                    targets = [(path, None)]
                else:
                    await status.edit_text("✂️ الفيديو طويل — جاري تقسيمه لأجزاء...")
                    tmpdir = Path(tmp)
                    parts = await loop.run_in_executor(
                        None, split_video, path, tmpdir, MAX_MB
                    )
                    if not parts:
                        await status.edit_text(
                            "❌ الفيديو كبير وما كدرت أقسّمه."
                        )
                        return
                    targets = [
                        (p, f"🎬 الجزء {i+1} من {len(parts)}\n📢 " + channel_url())
                        for i, p in enumerate(parts)
                    ]
                await status.edit_text("📤 جاري الإرسال...")
                for i, (vp, cap) in enumerate(targets):
                    with open(vp, "rb") as f:
                        await update.message.reply_video(
                            video=f,
                            caption=cap or ("🎬 تفضل — حمّلته لك\n📢 " + channel_url()),
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
