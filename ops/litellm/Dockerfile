FROM ghcr.io/berriai/litellm@sha256:20b5044b619055374061a6d5b7b08754cad75aeabbf82ddf4f69cc0cf80ddaf4
LABEL org.opencontainers.image.description="Existing LiteLLM 1.98.0 with October 2026 CA trust remediation only"
COPY certifi-2026.10.19-py3-none-any.whl install_certifi.py verify_trust.py impacted-intermediates.json /audit/
RUN apk --no-cache add --upgrade ca-certificates-bundle=20260909-r3
RUN /app/.venv/bin/python -S /audit/install_certifi.py
RUN /app/.venv/bin/python -S /audit/verify_trust.py
