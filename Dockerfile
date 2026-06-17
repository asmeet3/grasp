FROM python:3.12-slim

WORKDIR /app

# Install git (needed by GitPython)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Configure git
RUN git config --global user.name "Grasp Bot" && \
    git config --global user.email "grasp@company.com"

# Copy application code
COPY . .

# Install dependencies (non-editable for Docker)
RUN pip install --no-cache-dir .

# Create data directories
RUN mkdir -p knowledge_repo chroma_data checkpoints

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/api/status')" || exit 1

CMD ["python", "main.py"]
