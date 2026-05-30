FROM python:3.11-slim-bookworm

# Install LibreOffice & Java (Required for some Office formats)
# We also install 'gosu' to handle step-down from root if needed, 
# but here we'll just use a standard non-root user.
RUN apt-get update && apt-get install -y \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    libreoffice-draw \
    default-jre-headless \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -r docubrin && useradd -r -g docubrin -d /app -s /sbin/nologin docubrin

WORKDIR /app

# Create temp directory and set ownership
RUN mkdir -p /tmp/docubrin && chown -R docubrin:docubrin /tmp/docubrin && chmod 770 /tmp/docubrin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app /app
RUN chown -R docubrin:docubrin /app

# Switch to non-root user
USER docubrin

# Expose port for FastAPI
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
