# ===== Base =====
FROM python:3.11-slim

# Sistem bağımlılıkları + Chromium + chromedriver
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    fonts-liberation libnss3 libxss1 libasound2 libatk1.0-0 libcups2 \
    libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libxdamage1 libxrandr2 \
    libxcomposite1 libxfixes3 libxkbcommon0 libxext6 libx11-xcb1 libxi6 \
    curl unzip gnupg wget \
 && rm -rf /var/lib/apt/lists/*

# Ortam değişkenleri: Debian/Chromium yolları
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER=/usr/bin/chromedriver

# Python bağımlılıkları
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install openpyxl

# Kod
COPY . /app
WORKDIR /app

# Uygulama
CMD ["uvicorn", "google_patent_scraper:app", "--host", "0.0.0.0", "--port", "8000"]
