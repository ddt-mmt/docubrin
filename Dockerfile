FROM python:3.11-slim-bookworm

# Install LibreOffice & Java (Required for some Office formats)
RUN apt-get update && apt-get install -y \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    default-jre-headless \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create temp directory with restricted permissions
RUN mkdir -p /tmp/docubrin && chmod 777 /tmp/docubrin

COPY ./app /app

# Expose port for FastAPI
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
