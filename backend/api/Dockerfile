# Constitutional Assistant - Backend Docker image для Render.com
# Устанавливает Python-зависимости + Tesseract OCR (рус/каз/англ) для полноценной работы OCR

FROM python:3.11-slim

# Системные зависимости: Tesseract OCR + языковые пакеты (рус, каз, англ)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-kaz \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render передаёт порт через переменную окружения $PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
