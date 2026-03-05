# Research Agent

The research agent powers `scan.zech.sh` — a search interface that classifies user queries and either redirects them (URL, web search) or runs a multi-phase research pipeline that produces cited markdown answers. It operates in two modes: **lite** (fast, budget-constrained) and **deep** (thorough, multi-iteration with topic spawning).

Both modes share the same three-phase architecture. Deep mode simply gets more budget, more topics, more sources per topic, and an evaluation loop that can spawn follow-up research threads.

## Architecture

Every research job follows three phases:

```
Plan → Research → Articulate
```

**Phase 1 — Plan.** A planning LLM receives the user's question (with datetime context and conversation history) and decomposes it into 2-8 independent research topics, each with starting search queries. The planning reasoning is streamed to the user in real time.

**Phase 2 — Research.** All topics run concurrently via `asyncio.gather`. Each topic iterates through: Brave Search → LLM result filtering → robots.txt check → Jina Reader fetch → LLM extraction → evaluation. Deep mode can loop multiple iterations per topic and spawn new topics discovered during research. A second wave runs any spawned topics if budget remains.

**Phase 3 — Articulate.** All accumulated knowledge entries (grouped by research thread) are passed to an articulation LLM that streams a final markdown response with inline `[n]` citations and a sources list.

### Background Task Runner

Research pipelines run as `asyncio.Task`s managed by `controllers/scan.py`. A dictionary `_active_research` maps `chat_id → Task`. The web UI and API both use `_start_pipeline_task()` to launch pipelines, which prevents duplicate tasks for the same chat. Pipeline events are pushed to users via the notification system (SSE-based, `notify_user`), and the final assistant response is persisted as a `ChatMessage` with `events_json` and `usage_json`.

## Query Classification

When a user submits a query to `/search`, it's classified by `classify_query()` (Gemini Flash Lite) into one of:

| Classification | Action |
|---|---|
| `URL` | Redirect to the URL (strips/adds `https://`) |
| `SEARCH` | Redirect to Google (`google.com/search?q=...`) |
| `RESEARCH` | Create a chat session, run the **lite** pipeline |
| `DEEP_RESEARCH` | Create a chat session, run the **deep** pipeline |

The classification can be overridden via query parameter: `?mode=deep`, `?mode=discover` (lite), or `?mode=search`.

## Pipeline Phases

### Phase 1: Planning

The planning LLM receives:
- Current date/time in the user's timezone
- Conversation history (for multi-turn chats)
- The user's question

It outputs exploratory reasoning prose (streamed to the user) followed by a `TOPICS:` directive containing a JSON array of research topics. Each topic has a label, description, and starting search queries.

**Deep mode** uses `PLANNING_PROMPT` — asks for 3-6 topics covering different dimensions, including contrarian/critical perspectives and primary/technical sources. Reasoning is 2-4 paragraphs (150-300 words).

**Lite mode** uses `LIGHT_PLANNING_PROMPT` — asks for 2-4 topics with exactly one search query each. Reasoning is 2-3 sentences.

Parsing tries three strategies: regex for `TOPICS: [JSON]`, markdown code block extraction, and bare JSON array detection. If all fail, a single fallback topic uses the raw user query.

Source: `controllers/deep_research_agent.py` — `_plan()`, `_parse_plan_result()`

### Phase 2: Research

Each topic runs as an independent coroutine in `_research_topic()`. Within a topic:

1. **Brave Search** — queries are executed against the Brave Search API. Duplicate queries (normalized, case-insensitive) are skipped across all topics via a shared `queries_searched` set.

2. **LLM Filtering** — search results are ranked by a `FilteredResults` agent (Flash Lite) that excludes SEO spam, paywalled pages, duplicates of known content, and thin aggregators. Results are returned ranked by relevance, and the top N are selected for fetching.

3. **robots.txt Check** — each URL is checked against cached robots.txt rules before fetching. The checker respects rules for multiple AI user agents (GPTBot, ClaudeBot, Google-Extended, etc.) — if any are blocked, the URL is skipped.

4. **Jina Reader Fetch** — URLs are fetched as markdown via `r.jina.ai/{url}`. Responses are cached in Redis (or in-memory fallback). A token-bucket rate limiter (burst: 5, interval: 0.5s) prevents API overload. Failed fetches retry up to 3 times with exponential backoff.

5. **Extraction** — each fetched document (truncated to 20,000 chars) is processed by the extraction model (Flash, minimal thinking) to pull out key facts, numbers, dates, and quotes relevant to the query. Extractions are capped at 1,200 chars.

6. **Evaluation** (deep mode only) — after processing all queries for a topic, a `TopicEvaluation` agent (Flash Lite) decides whether to continue researching. It can provide refined queries for another iteration or spawn an entirely new topic. The loop continues until: max sources reached, budget exhausted, evaluator says stop, or no more queries.

After wave 1 completes, any spawned topics run as wave 2 (also concurrent), subject to budget and topic caps.

**Knowledge compression** runs if accumulated entries exceed `max_knowledge_chars`. Oldest entries are compressed first (Flash, minimal thinking) targeting roughly half their original length.

Source: `controllers/deep_research_agent.py` — `_research_topic()`, `_search_and_extract_query()`, `_filter_results()`, `_evaluate_topic_progress()`, `_compress_knowledge()`

### Phase 3: Articulation

The articulation LLM receives all accumulated knowledge grouped by research thread, a numbered source list, and the original question. It streams a markdown response with inline `[n]` citations.

**Deep mode** uses `ARTICULATION_PROMPT` with high thinking — produces expert briefings structured as narrative arguments. Opens with the sharpest finding, develops threads unevenly based on evidence strength, integrates counterarguments, and closes with implications.

**Lite mode** uses `LIGHT_ARTICULATION_PROMPT` with medium thinking — concise, decision-oriented answers. Leads with the most surprising finding, develops paragraphs proportional to evidence, includes the "yes, but," and closes forward.

Both prompts explicitly prohibit filler phrases, numbered sections, unnecessary bullet lists, and summary conclusions. They encourage markdown tables whenever comparing multiple items (tools, frameworks, options, etc.) to improve scannability.

Source: `controllers/deep_research_agent.py` — `_articulate()`

## Lite vs Deep Comparison

| Parameter | Lite | Deep |
|---|---|---|
| Planning prompt | `LIGHT_PLANNING_PROMPT` (2-3 sentences, 2-4 topics) | `PLANNING_PROMPT` (2-4 paragraphs, 3-6 topics) |
| Max topics | 4 | 8 |
| Max sources per topic | 5 | 10 |
| Max iterations per topic | 1 (no evaluation loop) | Unlimited (until evaluator stops or budget exhausted) |
| Spawned topics | 0 (disabled) | Up to 4 (max 10 total topics) |
| Research budget | $0.05 | $0.25 |
| Brave results per query | 5 | 15 |
| Jina reads per query | 3 | 5 |
| Max knowledge chars | 30,000 | 100,000 |
| Compress target | 20,000 | 70,000 |
| Articulation thinking | Medium | High |
| Articulation prompt | `LIGHT_ARTICULATION_PROMPT` | `ARTICULATION_PROMPT` |

Shared across both modes: extraction model (`gemini-3-flash-preview`), extract max chars (1,200), fetch max chars (20,000).

## Tools & Services

### Brave Search API

Rate-limited to 1 request/second globally via an async lock. Returns structured results with title, URL, and description. Configurable result count per query (5 for lite, 15 for deep).

Source: `controllers/brave_search.py`

### Jina Reader

Converts web pages to clean markdown via `https://r.jina.ai/{url}`. Rate-limited with a token-bucket limiter (burst: 5, interval: 0.5s). Retries up to 3 times with exponential backoff on failures and 429s. Supports optional Bearer auth via `JINA_API_KEY`. Responses are cached in Redis.

Source: `controllers/deep_research_agent.py` — `_jina_fetch()`, `_JinaRateLimiter`

### robots.txt Checker

Fetches and parses robots.txt per RFC 9309. Checks rules for multiple user agents simultaneously — our bot plus GPTBot, ChatGPT-User, ClaudeBot, Claude-Web, Anthropic-AI, and Google-Extended. If **any** of those agents are blocked for a path, the URL is skipped (ethical AI crawling). Also respects `ai-input: no` comment hints.

Parsed rules are cached in the database (`RobotsTxtCache` model) and refreshed every 24 hours. Crawl-delay directives are honored (defaults to 10 seconds when unspecified).

Source: `controllers/robots.py`

### Domain Throttling & Response Caching

Per-domain rate limiting uses Redis (`scan:ratelimit:{domain}` keys with NX + expiry) with an in-memory fallback. Default delay is 10 seconds between requests to the same domain, overridden by robots.txt Crawl-delay.

Response caching stores fetched content in Redis (`scan:cache:{sha256(url)[:16]}` keys) with TTL derived from Cache-Control/Expires headers (default: 24 hours). Cached content is capped at 500,000 chars. Falls back to in-memory dict when Redis is unavailable.

Source: `controllers/domain_throttle.py`

## Models

| Model | ID | Role |
|---|---|---|
| Gemini 3 Flash | `gemini-3-flash-preview` | Planning, extraction, articulation (both modes) |
| Gemini 3.1 Flash Lite | `gemini-3.1-flash-lite-preview` | Query classification, search result filtering, topic evaluation |
| Gemini 3 Pro | `gemini-3-pro-preview` | Chat title generation |

All models are accessed via `google-genai` SDK (`genai.Client`) for direct API calls and `pydantic-ai` (`GoogleModel` + `GoogleProvider`) for agent-based calls. A single shared provider and client are cached via `@lru_cache`.

Source: `controllers/llm.py`

## Data Model

### ChatSession

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key (from `Base`) |
| `user_id` | UUID | Owner |
| `title` | String(500) | LLM-generated title |
| `mode` | String(20) | `"research"` or `"deep_research"` |
| `last_notification_at` | Float (nullable) | Unix timestamp of last SSE notification push, used as replay cursor |
| `created_at` | DateTime | From `Base` |
| `updated_at` | DateTime | From `Base` |

### ChatMessage

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key (from `Base`) |
| `chat_id` | UUID | FK to `ChatSession` |
| `role` | String(20) | `"user"` or `"assistant"` |
| `content` | Text | Message body (markdown for assistant) |
| `events_json` | Text | JSON array of pipeline events (stages, searches, fetches, usage) |
| `usage_json` | Text | JSON object with token counts and costs by phase |
| `created_at` | DateTime | From `Base` |

Source: `models/chat.py`

## Programmatic API

All API endpoints require a Bearer API key in the `Authorization` header. Keys are SHA-256 hashed and stored in the `ApiKey` model. The `last_used_at` timestamp is updated on every authenticated request.

Source: `controllers/scan_api.py`, `controllers/api_auth.py`

### Start a Research Job

```
POST /api/research
Authorization: Bearer sk_...

{
  "query": "How does TCP congestion control work?",
  "mode": "lite"    // "lite" (default) or "deep"
}
```

**Response (201):**

```json
{
  "id": "a1b2c3d4-...",
  "url": "/chat/a1b2c3d4-...",
  "title": "TCP Congestion Control Mechanisms"
}
```

The pipeline starts immediately as a background task.

### Get Research Status & Result

```
GET /api/research/{id}
Authorization: Bearer sk_...
```

**Response (200):**

```json
{
  "id": "a1b2c3d4-...",
  "status": "completed",
  "title": "TCP Congestion Control Mechanisms",
  "mode": "research",
  "created_at": "2025-06-15T10:30:00",
  "result": {
    "content": "# TCP Congestion Control\n\n...",
    "sources": [
      "https://example.com/tcp-internals",
      "https://example.com/rfc-5681"
    ],
    "usage": {
      "research": { "input_tokens": 15000, "output_tokens": 3000, "input_cost": "0.0012", "output_cost": "0.0003" },
      "total": { "input_tokens": 25000, "output_tokens": 8000, "input_cost": "0.0020", "output_cost": "0.0010" }
    }
  }
}
```

**Status values:** `pending`, `running`, `completed`, `failed`

When status is `pending` or `running`, `result` is `null`. Poll until `completed` or `failed`.

## Crash Recovery

Two crash recovery paths handle orphaned pipelines (server restart while a pipeline was running):

1. **Web UI** (`controllers/scan.py` — `chat_view`): When loading a chat where the last message is from the user but no background task is active, the pipeline is restarted automatically via `_start_pipeline_task()`.

2. **API** (`controllers/scan_api.py` — `get_research`): When a GET request finds user messages but no assistant response and no active task, the pipeline is restarted and status is returned as `"pending"`.

Both paths rely on `_active_research` (an in-memory dict), so recovery only triggers after a process restart clears that dict while the database still shows an incomplete conversation.

## Pipeline Events

Events are emitted via an `asyncio.Queue` and forwarded to users as SSE notifications:

| Event | Description |
|---|---|
| `StageEvent(stage)` | Phase transition: `"reasoning"`, `"researching"`, `"responding"` |
| `DetailEvent("research", {topic})` | New research group started for a topic |
| `DetailEvent("search", {topic, query})` | Brave search initiated |
| `DetailEvent("search_done", {topic, query, num_results})` | Search completed |
| `DetailEvent("fetch", {topic, url})` | Jina fetch initiated |
| `DetailEvent("fetch_done", {topic, url, content?, failed?, usage?})` | Fetch/extraction completed or failed |
| `DetailEvent("result", {topic, urls, num_sources})` | Topic research group completed |
| `DetailEvent("reasoning", {text})` | Planning reasoning text chunk |
| `DetailEvent("usage", {research, extraction, total, budget})` | Final token/cost accounting |
| `TextEvent(text)` | Articulation text chunk (streamed) |
| `DoneEvent` | Pipeline completed successfully |
| `ErrorEvent(error)` | Pipeline error |
