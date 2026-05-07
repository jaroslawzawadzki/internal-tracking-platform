FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app


# 1. Odświeżamy listy
RUN apt-get update

# 2. Instalujemy podstawowe narzędzia i certyfikaty (to pomaga przy błędach 100)
RUN apt-get install -y --no-install-recommends ca-certificates wget gnupg

# 3. Instalujemy biblioteki dla przeglądarki (Playwright)
RUN apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxext6 \
    libxfixes3 libxrandr2 libgbm1 libasound2

# 4. Instalujemy biblioteki dla PDF (WeasyPrint)

# 4. Instalujemy biblioteki dla PDF (WeasyPrint) - Wersja elastyczna dla Debiana Trixie
RUN apt-get install -y --no-install-recommends \
    libpango-1.0* \
    libharfbuzz0b \
    libpangoft2-1.0* \
    libpangocairo-1.0* \
    libgdk-pixbuf2.0* \
    libffi-dev \
    shared-mime-info
    
RUN pip install playwright
RUN playwright install chromium
RUN playwright install-deps

# 5. Czyścimy śmieci
RUN apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# UWAGA: kopiujemy kod django z folderu app (nawet jeśli na razie jest pusty)
COPY app /app

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]