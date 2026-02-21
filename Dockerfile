FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Store the SQLite DB in a mounted volume so it persists across restarts
ENV DB_PATH=/app/data/lol_bot.db

RUN mkdir -p /app/data

CMD ["python", "bot.py"]
