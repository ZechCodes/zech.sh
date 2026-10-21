# LiteLLM proxy CA remediation, 2026-10-21

Preserves the deployed LiteLLM 1.98.0 image and all proxy configuration.
Base image: ghcr.io/berriai/litellm@sha256:20b5044b619055374061a6d5b7b08754cad75aeabbf82ddf4f69cc0cf80ddaf4.

Changes only the Wolfi ca-certificates-bundle package to signed version 20260909-r3
and certifi to the dependency-free 2026.10.19 wheel. Wheel SHA256:
42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b.
Wheel source: https://pypi.org/project/certifi/2026.10.19/.
Public test certificates: https://emudhra.we-have-a-problem.com/impacted-intermediates.json.

Build: `nice -n 10 docker build --pull=false -t local/litellm:1.98.0-ca-20261021 .`
The Dockerfile runs tests using Python -S, rejecting all 16 compromised intermediates
and cross-signs against both independent bundles, and checks ordinary HTTPS.
The unmodified base fails the patched-certifi assertion.

Initial build index: sha256:0d50d9bd06ce14d2ec1abb94db7b6f7b0bd764a59bd38e30a5e189f514e0d652.
Deploy immutable local RepoDigest with `docker compose up -d --no-deps --pull never litellm`.
Keep credentials and compose backups only on the server; never add them here.
`verify_api.py` runs on the host, reads credentials from Docker metadata without
printing them, checks health, unauthorized rejection and authorized model listing.
Its optional `--stream` makes one short provider request.
The maintained copies use a fixed HTTPS allowlist, preserving production
HTTPS_PROXY/Squid routing, and a subprocess-based `-S` runtime verifier;
no source rewriting or `exec` is needed.
The explicit TLS context requires certificate validation and hostname checking.

Rollback: restore the server-only pre-remediation compose backup, replace its
floating LiteLLM image with the base digest above, and recreate only litellm.
No database or volumes are changed. A restored old image has outdated CA trust.

## Deployment validation

Deployed at 2026-10-21 18:31 UTC; healthy after normal cold startup (~3m40s,
including Prisma setup on the 1GB host), zero restarts. Both trust bundles and
default OpenSSL trust reject all 16 compromised intermediate/cross-sign fixtures.
The existing Squid policy blocks PyPI in production; runtime HTTPS was repeated
against allowed api.openai.com using verify_runtime_trust.py. Build-time HTTPS
also passed against PyPI.

Liveliness/readiness 200, missing API credentials rejected, authenticated model
listing works, and one short openai/gpt-4o-mini streaming completion reached DONE.
Wildcard-expanded model listing changed 400 to396 after restart; configuration,
OIDC environment values, command, mounts, memory limits, and database were preserved.
An initially auto-selected Codex model returned404 on the chat endpoint; the
compatible gpt-4o-mini smoke test passed. Interactive OIDC login was not exercised.

No compromised October image is present in the local image inventory; retained
upstream image timestamps are August22 (main-stable) and August30 (main-latest).
This inventory is evidence about current local state, not a complete pull history.
No pip/uv distribution was found in /app or /usr; the runtime uses its venv certifi.

Server-only backups: docker-compose.yml.pre-ca-remediation-20261021T182739Z
and docker-compose.yml.pre-ca-derivative-pinned, both mode0600.
