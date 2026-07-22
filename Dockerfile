FROM python:3.12-slim

WORKDIR /app

# GitPython shells out to Git for repository operations.
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

RUN git config --global user.name "Grasp Bot" && \
    git config --global user.email "grasp@company.com"

COPY . .

RUN pip install --no-cache-dir .

RUN mkdir -p knowledge_repo chroma_data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/api/status')" || exit 1

CMD ["python", "main.py"]
