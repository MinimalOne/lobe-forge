FROM python:3.12-slim

ARG VERSION=1.9.0

LABEL org.opencontainers.image.source="https://github.com/MinimalOne/lobe-forge" \
      org.opencontainers.image.description="Self-hosted REST API & MCP server for lobe-forge LLM wikis" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY .claude-plugin/plugin.json /app/.claude-plugin/plugin.json
COPY scripts/ /app/scripts/
COPY templates/ /app/templates/
COPY server/app.py /app/server/app.py

ENV LOBES_DIR=/data/lobes \
    LOBE_HOST=0.0.0.0 \
    LOBE_PORT=8080 \
    LOBE_SERVER_VERSION="${VERSION}"

VOLUME ["/data/lobes"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1

CMD ["python3", "/app/server/app.py"]
