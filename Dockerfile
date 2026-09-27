# ModelXGit: the backend + web app, with IBM Bob Shell, in one container (used by render.yaml).
#   docker build -t modelxgit .
#   docker run -p 8000:8000 --env-file .env -v modelxgit-data:/data modelxgit
FROM node:22-bookworm-slim

# Python for the backend, git for cloning. Bob Shell needs Node 22 or newer (this image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# IBM Bob Shell, the version the app was tested with.
RUN npm install -g bobshell@2.0.5 && npm cache clean --force

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a normal user; the entrypoint only fixes ownership of the data disk, then drops root.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chmod +x /app/deploy/entrypoint.sh

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    BOB_CLIENT=shell \
    WORKSPACE_DIR=/data/workspace \
    PORT=8000

EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
