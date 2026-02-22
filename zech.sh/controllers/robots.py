"""Robots.txt fetcher, parser, and URL permission checker.

Fetches and parses robots.txt files, caching results in the database.
Rules are gathered for our user agent as well as Google, OpenAI, and
Anthropic user agents. If any of those agents are blocked, we treat
ourselves as blocked too (ethical AI crawling).

Parsed rules are cached in the database and reprocessed after 24 hours.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.robots_txt_cache import RobotsTxtCache

logger = logging.getLogger(__name__)

USER_AGENT = (
    "scan-zech-sh-research-bot/1.0 (+https://scan.zech.sh; admin@zech.sh)"
)
USER_AGENT_SHORT = "scan-zech-sh-research-bot"

# User agents whose rules we also respect. If a site blocks any of these,
# we treat ourselves as blocked too.
WATCHED_USER_AGENTS: list[str] = [
    "scan-zech-sh-research-bot",
    "scanzechresearchbot",
    "gptbot",
    "chatgpt-user",
    "claudebot",
    "claude-web",
    "anthropic-ai",
    "google-extended",
]

_RECHECK_INTERVAL = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Robots.txt parser
# ---------------------------------------------------------------------------


@dataclass
class RobotsRule:
    """A single Allow or Disallow directive."""

    path: str
    allowed: bool


@dataclass
class RobotsGroup:
    """A group of rules for one or more user-agents."""

    user_agents: list[str] = field(default_factory=list)
    rules: list[RobotsRule] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class ParsedRobotsTxt:
    """Fully parsed robots.txt with groups and AI hints."""

    groups: list[RobotsGroup] = field(default_factory=list)
    ai_input: bool | None = None  # None = not specified
    ai_train: bool | None = None  # None = not specified


def parse_robots_txt(content: str) -> ParsedRobotsTxt:
    """Parse a robots.txt file into structured groups and AI hints.

    Follows RFC 9309 semantics: groups are delimited by User-agent lines,
    and each group applies to the user-agents listed before the first
    Allow/Disallow directive.
    """
    result = ParsedRobotsTxt()
    current_group: RobotsGroup | None = None
    in_rules = False

    for raw_line in content.splitlines():
        # Strip comments (but capture AI hint comments first)
        comment_match = re.match(r"^\s*#\s*(.*)", raw_line)
        if comment_match:
            hint = comment_match.group(1).strip().lower()
            if hint.startswith("ai-input:"):
                val = hint.split(":", 1)[1].strip()
                result.ai_input = val == "yes"
            elif hint.startswith("ai-train:"):
                val = hint.split(":", 1)[1].strip()
                result.ai_train = val == "yes"
            continue

        # Remove inline comments
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        # Parse directive
        if ":" not in line:
            continue

        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()

        if directive == "user-agent":
            if in_rules:
                # Start a new group
                current_group = RobotsGroup()
                result.groups.append(current_group)
                in_rules = False

            if current_group is None:
                current_group = RobotsGroup()
                result.groups.append(current_group)

            current_group.user_agents.append(value.lower())

        elif directive == "disallow":
            if current_group is None:
                current_group = RobotsGroup(user_agents=["*"])
                result.groups.append(current_group)
            current_group.rules.append(RobotsRule(path=value, allowed=False))
            in_rules = True

        elif directive == "allow":
            if current_group is None:
                current_group = RobotsGroup(user_agents=["*"])
                result.groups.append(current_group)
            current_group.rules.append(RobotsRule(path=value, allowed=True))
            in_rules = True

        elif directive == "crawl-delay":
            if current_group is None:
                current_group = RobotsGroup(user_agents=["*"])
                result.groups.append(current_group)
            try:
                current_group.crawl_delay = float(value)
            except ValueError:
                pass

    return result


def _ua_matches(group_ua: str, target_ua: str) -> bool:
    """Check if a group user-agent string matches a target user-agent.

    Per RFC 9309, matching is case-insensitive substring matching of the
    product token. The wildcard '*' matches everything.
    """
    if group_ua == "*":
        return True
    # Substring match (case-insensitive, already lowered)
    return group_ua in target_ua


def _find_matching_group(
    parsed: ParsedRobotsTxt, user_agent: str
) -> RobotsGroup | None:
    """Find the most specific matching group for a user-agent.

    Per RFC 9309, the most specific matching group wins (longest UA match).
    Falls back to the '*' group if no specific match is found.
    """
    ua_lower = user_agent.lower()
    best_match: RobotsGroup | None = None
    best_match_len = 0
    wildcard_group: RobotsGroup | None = None

    for group in parsed.groups:
        for group_ua in group.user_agents:
            if group_ua == "*":
                wildcard_group = group
            elif _ua_matches(group_ua, ua_lower):
                if len(group_ua) > best_match_len:
                    best_match = group
                    best_match_len = len(group_ua)

    return best_match or wildcard_group


def _path_matches(rule_path: str, url_path: str) -> bool:
    """Check if a robots.txt path pattern matches a URL path.

    Supports '*' wildcards and '$' end-of-string anchor per Google's spec.
    An empty path matches nothing (per RFC 9309, empty Disallow = allow all).
    """
    if not rule_path:
        return False  # Empty path matches nothing

    # Convert robots.txt path pattern to regex
    pattern = re.escape(rule_path)
    pattern = pattern.replace(r"\*", ".*")
    if pattern.endswith(r"\$"):
        pattern = pattern[:-2] + "$"
    else:
        pattern = pattern + ".*"
    pattern = "^" + pattern

    return bool(re.match(pattern, url_path))


def is_path_allowed(parsed: ParsedRobotsTxt, path: str) -> bool:
    """Check if a path is allowed for any of our watched user agents.

    We check against all watched UAs. If ANY watched UA is blocked,
    we treat the path as blocked for ethical reasons.
    """
    for ua in WATCHED_USER_AGENTS:
        group = _find_matching_group(parsed, ua)
        if group is None:
            continue

        if not group.rules:
            continue

        # Find the most specific matching rule (longest path wins)
        best_rule: RobotsRule | None = None
        best_len = -1

        for rule in group.rules:
            if _path_matches(rule.path, path):
                if len(rule.path) > best_len:
                    best_rule = rule
                    best_len = len(rule.path)

        if best_rule is not None and not best_rule.allowed:
            return False

    # Also check AI hints
    if parsed.ai_input is False:
        return False

    return True


def get_crawl_delay(parsed: ParsedRobotsTxt) -> float | None:
    """Get the most restrictive crawl delay across all watched UAs.

    Returns the largest crawl-delay found, or None if none specified.
    """
    max_delay: float | None = None
    for ua in WATCHED_USER_AGENTS:
        group = _find_matching_group(parsed, ua)
        if group and group.crawl_delay is not None:
            if max_delay is None or group.crawl_delay > max_delay:
                max_delay = group.crawl_delay
    return max_delay


def _is_ai_blocked(parsed: ParsedRobotsTxt) -> bool:
    """Check if the site explicitly blocks AI bots.

    Returns True if any watched AI-specific UA is fully blocked
    (Disallow: /) or if ai-input is explicitly set to no.
    """
    if parsed.ai_input is False:
        return True

    ai_uas = ["gptbot", "chatgpt-user", "claudebot", "claude-web",
              "anthropic-ai", "google-extended"]

    for ua in ai_uas:
        group = _find_matching_group(parsed, ua)
        if group is None:
            continue
        # Check if there's a blanket Disallow: /
        for rule in group.rules:
            if rule.path == "/" and not rule.allowed:
                return True
    return False


def _serialize_parsed(parsed: ParsedRobotsTxt) -> str:
    """Serialize parsed robots.txt to JSON for database storage."""
    data = {
        "groups": [
            {
                "user_agents": g.user_agents,
                "rules": [
                    {"path": r.path, "allowed": r.allowed} for r in g.rules
                ],
                "crawl_delay": g.crawl_delay,
            }
            for g in parsed.groups
        ],
        "ai_input": parsed.ai_input,
        "ai_train": parsed.ai_train,
    }
    return json.dumps(data)


def _deserialize_parsed(json_str: str) -> ParsedRobotsTxt:
    """Deserialize parsed robots.txt from JSON."""
    data = json.loads(json_str)
    return ParsedRobotsTxt(
        groups=[
            RobotsGroup(
                user_agents=g["user_agents"],
                rules=[
                    RobotsRule(path=r["path"], allowed=r["allowed"])
                    for r in g["rules"]
                ],
                crawl_delay=g.get("crawl_delay"),
            )
            for g in data["groups"]
        ],
        ai_input=data.get("ai_input"),
        ai_train=data.get("ai_train"),
    )


# ---------------------------------------------------------------------------
# Database-backed robots.txt cache
# ---------------------------------------------------------------------------


async def _fetch_robots_txt(domain: str) -> str:
    """Fetch robots.txt from a domain. Returns empty string on failure."""
    url = f"https://{domain}/robots.txt"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=10.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.text
            # 4xx/5xx → treat as no robots.txt (allow all)
            return ""
        except httpx.RequestError:
            return ""


async def get_robots_rules(
    domain: str, db_session: AsyncSession
) -> tuple[ParsedRobotsTxt, RobotsTxtCache]:
    """Get parsed robots.txt rules for a domain, using the DB cache.

    Returns a tuple of (parsed_rules, cache_record). If the cache is
    stale (older than 24 hours), a background refresh is attempted.
    """
    now = datetime.now(timezone.utc)

    # Check cache
    result = await db_session.execute(
        select(RobotsTxtCache).where(RobotsTxtCache.domain == domain)
    )
    cached = result.scalar_one_or_none()

    if cached is not None and cached.next_check_at > now:
        # Cache is fresh
        parsed = _deserialize_parsed(cached.rules_json)
        return parsed, cached

    # Cache is stale or missing — fetch and parse
    raw_content = await _fetch_robots_txt(domain)
    parsed = parse_robots_txt(raw_content)
    crawl_delay = get_crawl_delay(parsed)
    ai_blocked = _is_ai_blocked(parsed)
    rules_json = _serialize_parsed(parsed)

    if cached is not None:
        # Update existing record
        cached.raw_content = raw_content
        cached.rules_json = rules_json
        cached.crawl_delay = crawl_delay
        cached.ai_blocked = ai_blocked
        cached.fetched_at = now
        cached.next_check_at = now + _RECHECK_INTERVAL
    else:
        # Create new record
        cached = RobotsTxtCache(
            domain=domain,
            raw_content=raw_content,
            rules_json=rules_json,
            crawl_delay=crawl_delay,
            ai_blocked=ai_blocked,
            fetched_at=now,
            next_check_at=now + _RECHECK_INTERVAL,
        )
        db_session.add(cached)

    await db_session.flush()
    return parsed, cached


async def check_url_allowed(
    url: str, db_session: AsyncSession
) -> tuple[bool, float]:
    """Check if a URL is allowed by robots.txt and return the crawl delay.

    Returns:
        (allowed, crawl_delay_seconds) — crawl_delay defaults to 10.0
        if no Crawl-delay directive is found.
    """
    parsed_url = urlparse(url)
    domain = parsed_url.hostname
    if not domain:
        return False, 10.0

    path = parsed_url.path or "/"

    parsed, cached = await get_robots_rules(domain, db_session)

    allowed = is_path_allowed(parsed, path)
    delay = cached.crawl_delay if cached.crawl_delay is not None else 10.0

    return allowed, delay
