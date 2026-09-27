# ModelXGit: the backend + web app, with IBM Bob Shell, in one container (used by render.yaml).
#   docker build -t modelxgit .
#   docker run -p 8000:8000 --env-file .env -v modelxgit-data:/data modelxgit
FROM node:22-bookworm-slim

# Python for the backend, git for cloning, curl for the Bob Shell download.
# Bob Shell needs Node 22.15 or newer (this image has the latest 22.x).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# IBM Bob Shell, the version the app was tested with. It isn't on the npm registry: IBM publishes
# a .tgz, installed the same way as IBM's own script (bob.ibm.com/download/bobshell.sh), with the
# checksum pinned so a changed file is never installed.
ARG BOBSHELL_VERSION=2.0.5
ARG BOBSHELL_SHA256=eff232eb1b69f34f984ddd295e6960470058ca922b1c751879c5a8d06199f566
RUN curl -fsSL -o /tmp/bobshell.tgz \
       "https://s3.us-south.cloud-object-storage.appdomain.cloud/bob-shell/bobshell-${BOBSHELL_VERSION}.tgz" \
    && echo "${BOBSHELL_SHA256}  /tmp/bobshell.tgz" | sha256sum -c - \
    && npm install --registry=https://registry.npmjs.org/ --allow-scripts=@officecli/officecli \
       --progress=false --loglevel=error -g /tmp/bobshell.tgz \
    && bob --version \
    && rm -f /tmp/bobshell.tgz && npm cache clean --force

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
