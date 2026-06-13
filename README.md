# zech.sh

The source for [zech.sh](https://zech.sh) and its sibling subdomains. It is a
[Skrift](https://github.com/ZechCodes/Skrift) site: an async Python CMS built on
Litestar, extended here with custom themes, controllers, and AI tooling.

One Skrift app serves four sites off one codebase, routed by subdomain:

| Site | Theme | What it is |
|------|-------|------------|
| `zech.sh` | `town` | The home page: a top-down pixel-art town that animates Zech's day, plus the About / Work / Community pages. |
| `dump.zech.sh` | `dump` | The blog ("the dump"), code-heavy posts. |
| `scan.zech.sh` | `scan` | A smart search relay that classifies a query and runs an AI research pipeline. |
| `aichat.zech.sh` | `scan` | A real-time AI chat client with SSE notifications. |

## Stack

- **[Skrift](https://github.com/ZechCodes/Skrift)** on **Litestar** (ASGI, async)
- **PostgreSQL** (content, settings) and **Redis** (sessions, caching)
- **[pydantic-ai](https://ai.pydantic.dev/)** + Google / OpenAI models for the search and chat agents
- **uv** for dependency management, **Docker** + **Kubernetes** (DigitalOcean) for deploys

## Layout

```
app.yaml                Base config: domain, controllers, sites/subdomains, page types
app.development.yaml     Local-dev overrides (merged when SKRIFT_ENV=development)
compose.yaml             Local dev stack (app + Postgres + Redis)
Dockerfile               Production image (uv sync, then `skrift serve`)

content/                 Seed markdown for the About / Work / Community pages
controllers/             Custom Litestar controllers (see below)
models/                  Project-specific database models
templates/               Admin templates for the custom controllers
themes/                  One folder per theme: theme.yaml, templates/, static/
  town/                  The pixel-art town (home). See the engine note below.
  dump/                  The blog.
  scan/                  Search + chat UI.
  fusion/                Shared design system (fonts, tokens, effects) used by scan.
migrations/              Alembic migrations
k8s/                     Kubernetes manifests (deployment, ingress, redirects, HPA)
scripts/                 Small operational CLIs for the aichat feature
tests/                   pytest suite (search classifier, robots, throttling, etc.)
docs/                    Design notes (e.g. the research agent)
```

### Custom controllers

- `redirects` — `/discord` short link and the root `/favicon.ico`
- `health` — liveness/readiness probe
- `admin_*` — admin screens (post import, integrations, usage, API keys)
- `scan`, `scan_api` — the search relay and its research API
- `aichat` — the AI chat client, device pairing, and websocket
- plus the research/agent modules (`research_agent`, `deep_research_agent`,
  `chat_agent`, `brave_search`, `llm`, `domain_throttle`, ...)

### The town engine

`themes/town/static/js/world.js` is a self-contained HTML5 canvas renderer for
the home page: a tile world with BFS pathfinding, a day/night cycle, and
characters that follow daily routines. `themes/town/static/js/forest.js` is the
same idea behind the 404/error pages (a traveller walking a forest, making camp
at night). Both cap at 30fps and fall back to a single static frame under
`prefers-reduced-motion`.

## Running locally

Needs Docker and a `.env` (see `compose.yaml` for the variables it expects, such
as `DATABASE_URL` and `REDIS_URL`; AI features also need provider API keys).

```bash
docker compose up --build
```

The app serves on http://localhost:8888. `compose.yaml` mounts the source dirs,
so theme, template, and controller edits show up on refresh.

Run the tests with:

```bash
uv run pytest
```

## Deploying

Build the amd64 image, push it, and roll the Kubernetes deployment:

```bash
podman build --platform linux/amd64 -t zzmmrmn/zech-sh:<git-sha> .
podman push zzmmrmn/zech-sh:<git-sha>
kubectl -n zechcodes set image deployment/zech-sh zech-sh=zzmmrmn/zech-sh:<git-sha>
```

The manifests in `k8s/` define the deployment, the Traefik ingress and host
redirects, and autoscaling. The active theme is stored as a database setting
(`site_theme`), not in `app.yaml`.
