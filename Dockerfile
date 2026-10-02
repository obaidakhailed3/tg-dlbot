FROM python:3.12-slim

# ffmpeg ضروري لدمج الصوت/الفيديو من yt-dlp
# deno ضروري لـ yt-dlp لحل تحديات جافاسكربت الخاصة بيوتيوب (EJS challenge solving)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl unzip \
    && curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip -q /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

# عند كل تشغيل: حدّث yt-dlp (المواقع تغيّر حمايتها باستمرار) ثم شغّل البوت
CMD ["sh", "-c", "pip install -q -U yt-dlp && python bot.py"]
