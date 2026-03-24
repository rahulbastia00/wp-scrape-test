FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install-deps chromium

COPY . . 

RUN mkdir -p media data/whatsapp_profile

# MODE 1: Scraper (Active by default)
CMD ["python", "main.py", "--mode", "scrape", "--phone", "+91xxxxxxxxxx"]

# MODE 2: Broadcast (Comment out Mode 1 and uncomment this to use)
# CMD ["python", "main.py", "--mode", "send"]