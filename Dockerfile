
FROM python:3.12-slim

# ffmpeg ضروري لدمج الصوت/الفيديو من yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

# عند كل تشغيل: حدّث yt-dlp (المواقع تغيّر حمايتها باستمرار) ثم شغّل البوت
CMD ["sh", "-c", "pip install -q -U yt-dlp && python bot.py"]
