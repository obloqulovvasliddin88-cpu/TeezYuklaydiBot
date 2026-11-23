FROM python:3.12-slim

# ffmpeg — video/ovozni birlashtirish va MP3 uchun; deno — YouTube uchun yt-dlp talab qiladi
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
# yt-dlp har deployda eng yangi versiyaga yangilanadi (platformalar tez-tez o'zgaradi)
RUN pip install --no-cache-dir -U -r requirements.txt

COPY bot.py .
CMD ["python", "bot.py"]
