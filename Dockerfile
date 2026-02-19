# Azure Cloud Parity Bot – Hosted Agent Container
# Must be built with --platform linux/amd64 (Foundry hosted agent requirement)

FROM python:3.11-slim

# Prevents Python from writing .pyc files and buffers stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (leverages Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agents/ agents/
COPY clients/ clients/
COPY config/ config/
COPY models/ models/
COPY storage/ storage/
COPY utils/ utils/
COPY main.py .

# Create runtime directories
RUN mkdir -p data/features reports logs

# The hosting adapter (from_agent_framework) starts an HTTP server on port 8088
EXPOSE 8088

# Default: start HTTP server mode (not CLI mode)
CMD ["python", "main.py"]
