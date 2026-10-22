FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock.txt

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=builder /install /usr/local
COPY vkbot/ /app/vkbot/
COPY bot.py /app/bot.py
RUN useradd --create-home botuser && mkdir -p /app/vkbot/cache /app/.vkbottle && chown -R botuser:botuser /app
USER botuser
EXPOSE 8080
CMD ["python", "bot.py"]
