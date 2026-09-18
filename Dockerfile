# Data Product Recommendation Engine.
# Stdlib only, so the image is the interpreter plus this package: no wheel
# resolution, no transitive CVE surface, nothing to audit but Python itself.
FROM python:3.11-slim

LABEL org.opencontainers.image.title="Data Product Recommendation Engine" \
      org.opencontainers.image.description="Ranked, evidence-backed data product candidates. The engine proposes; a human decides." \
      org.opencontainers.image.licenses="Proprietary"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DPRE_WORKSPACE=/var/lib/dpre \
    DPRE_HOST=0.0.0.0 \
    DPRE_PORT=8000 \
    DPRE_LOG_FORMAT=json

WORKDIR /app
COPY pyproject.toml README.md ./
COPY dpre ./dpre
RUN pip install --no-cache-dir . && \
    groupadd --system --gid 10001 dpre && \
    useradd --system --uid 10001 --gid dpre --home /var/lib/dpre dpre && \
    mkdir -p /var/lib/dpre && chown -R dpre:dpre /var/lib/dpre && chmod 700 /var/lib/dpre

# The workspace holds client metadata and the decision ledger: mount an
# encrypted volume here and back it up with the rest of the client's records.
VOLUME ["/var/lib/dpre"]

USER dpre:dpre
EXPOSE 8000

# The container binds 0.0.0.0, so it refuses to start unless DPRE_TOKENS or
# DPRE_TRUSTED_PROXY is set: run it behind the TLS-terminating SSO proxy that
# docs/deployment.md describes, never on an open network.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys;\
u='http://127.0.0.1:'+os.environ.get('DPRE_PORT','8000')+'/api/v1/health';\
sys.exit(0 if urllib.request.urlopen(u,timeout=4).status==200 else 1)"

ENTRYPOINT ["dpre", "serve"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--workspace", "/var/lib/dpre"]
