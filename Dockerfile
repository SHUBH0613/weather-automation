# Official Microsoft Playwright image with Python & Chromium dependencies
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Copy app files
COPY . .

# Set environment
ENV PYTHONUNBUFFERED=1
ENV PORT=7860

# Default port for cloud (HuggingFace 7860, Render reads $PORT)
EXPOSE 7860 5000 10000

CMD ["python", "app.py"]
