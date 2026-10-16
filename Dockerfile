# NFSU Library Self-Service Kiosk Docker Container
# Runs 24/7 in the cloud (Render, Railway, VPS) independently of local laptop

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code & assets
COPY . .

# Expose Kiosk port
EXPOSE 8000

# Start Kiosk server
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
