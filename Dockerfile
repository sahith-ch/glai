# GLAI backend: the learner + hiring APIs on Supabase (roadmap_agent/api.py).
#   docker build -t glai-backend .
#   docker run -p 8080:8080 --env-file .env.production glai-backend
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GLAI_CODE_EXECUTION=off \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY roadmap_agent ./roadmap_agent
COPY main.py ./

# Bake the hiring search models into the image, so the first search doesn't download them.
# --build-arg BAKE_SEARCH_MODELS=0 leaves them out (~190 MB) for hosts that run keyword search only.
ARG BAKE_SEARCH_MODELS=1
RUN if [ "$BAKE_SEARCH_MODELS" = "1" ]; then python -c "from roadmap_agent import vector; assert vector.get() is not None"; fi

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 glai && chown -R glai /app  # uid 1000: what Hugging Face Spaces runs containers as
USER glai

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/api/health', timeout=4)"
CMD ["sh", "-c", "uvicorn roadmap_agent.api:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
