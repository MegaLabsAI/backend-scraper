# ✅ Python 3.11 slim image
FROM python:3.11-slim

# ========================
# 1. Sistem bağımlılıkları
# ========================
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    gnupg \
    fonts-liberation \
    libnss3 \
    libx11-6 \
    libx11-xcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxi6 \
    libxtst6 \
    libglib2.0-0 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libasound2 \
    libxrandr2 \
    libatk1.0-0 \
    libcups2 \
    && rm -rf /var/lib/apt/lists/*

# ========================
# 2. Google Chrome yükle
# ========================
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y google-chrome-stable

# ========================
# 3. Ortam ayarları
# ========================
ENV DISPLAY=:99

# ========================
# 4. Python bağımlılıkları
# ========================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install openpyxl  # ✅ pandas.to_excel için gerekli

# ========================
# 5. Kodları kopyala
# ========================
COPY . /app
WORKDIR /app

# ========================
# 6. Başlatma komutu
# ========================
CMD ["uvicorn", "google_patent_scraper:app", "--host", "0.0.0.0", "--port", "8000"]

# ✅ Chrome için env
ENV CHROME_BIN=/usr/bin/google-chrome