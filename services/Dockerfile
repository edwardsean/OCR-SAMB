FROM python:3.12-slim
# tesseract (+ Indonesian) and poppler are needed from phase 1–2; installed now so later phases don't rebuild the base layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
