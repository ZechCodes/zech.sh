"""Tests for the robots.txt parser and URL permission checker."""

import pytest

from controllers.robots import (
    ParsedRobotsTxt,
    RobotsGroup,
    RobotsRule,
    _find_matching_group,
    _is_ai_blocked,
    get_crawl_delay,
    is_path_allowed,
    parse_robots_txt,
)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParseRobotsTxt:
    def test_empty(self):
        parsed = parse_robots_txt("")
        assert parsed.groups == []
        assert parsed.ai_input is None
        assert parsed.ai_train is None

    def test_simple_global_allow(self):
        content = "User-agent: *\nDisallow:\n"
        parsed = parse_robots_txt(content)
        assert len(parsed.groups) == 1
        assert parsed.groups[0].user_agents == ["*"]
        assert len(parsed.groups[0].rules) == 1
        assert parsed.groups[0].rules[0].path == ""
        assert parsed.groups[0].rules[0].allowed is False

    def test_block_private(self):
        content = "User-agent: *\nDisallow: /private/\n"
        parsed = parse_robots_txt(content)
        assert len(parsed.groups[0].rules) == 1
        assert parsed.groups[0].rules[0].path == "/private/"
        assert parsed.groups[0].rules[0].allowed is False

    def test_specific_bot_rules(self):
        content = (
            "User-agent: ScanZechResearchBot\n"
            "Disallow: /\n"
            "Allow: /public-research/\n"
        )
        parsed = parse_robots_txt(content)
        assert len(parsed.groups) == 1
        group = parsed.groups[0]
        assert "scanzechresearchbot" in group.user_agents
        assert len(group.rules) == 2

    def test_multiple_groups(self):
        content = (
            "User-agent: GPTBot\n"
            "Disallow: /\n\n"
            "User-agent: ClaudeBot\n"
            "Disallow: /\n\n"
            "User-agent: *\n"
            "Disallow:\n"
        )
        parsed = parse_robots_txt(content)
        assert len(parsed.groups) == 3

    def test_crawl_delay(self):
        content = "User-agent: *\nCrawl-delay: 5\nDisallow:\n"
        parsed = parse_robots_txt(content)
        assert parsed.groups[0].crawl_delay == 5.0

    def test_ai_hints_in_comments(self):
        content = (
            "User-agent: *\n"
            "Disallow:\n"
            "# ai-input: no\n"
            "# ai-train: no\n"
        )
        parsed = parse_robots_txt(content)
        assert parsed.ai_input is False
        assert parsed.ai_train is False

    def test_ai_hints_yes(self):
        content = (
            "User-agent: *\n"
            "Disallow:\n"
            "# ai-input: yes\n"
            "# ai-train: yes\n"
        )
        parsed = parse_robots_txt(content)
        assert parsed.ai_input is True
        assert parsed.ai_train is True

    def test_inline_comments_stripped(self):
        content = "User-agent: * # all bots\nDisallow: /secret/ # no access\n"
        parsed = parse_robots_txt(content)
        assert parsed.groups[0].user_agents == ["*"]
        assert parsed.groups[0].rules[0].path == "/secret/"


# ---------------------------------------------------------------------------
# Group matching tests
# ---------------------------------------------------------------------------


class TestFindMatchingGroup:
    def test_wildcard_match(self):
        parsed = ParsedRobotsTxt(
            groups=[RobotsGroup(user_agents=["*"], rules=[])]
        )
        group = _find_matching_group(parsed, "scan-zech-sh-research-bot")
        assert group is not None
        assert group.user_agents == ["*"]

    def test_specific_match_preferred(self):
        wildcard_group = RobotsGroup(user_agents=["*"], rules=[])
        specific_group = RobotsGroup(
            user_agents=["scan-zech-sh-research-bot"], rules=[]
        )
        parsed = ParsedRobotsTxt(groups=[wildcard_group, specific_group])
        group = _find_matching_group(parsed, "scan-zech-sh-research-bot")
        assert group is specific_group

    def test_no_match_returns_wildcard(self):
        wildcard_group = RobotsGroup(user_agents=["*"], rules=[])
        gpt_group = RobotsGroup(user_agents=["gptbot"], rules=[])
        parsed = ParsedRobotsTxt(groups=[wildcard_group, gpt_group])
        group = _find_matching_group(parsed, "scan-zech-sh-research-bot")
        assert group is wildcard_group

    def test_substring_matching(self):
        group = RobotsGroup(user_agents=["gptbot"], rules=[])
        parsed = ParsedRobotsTxt(groups=[group])
        # "gptbot" is a substring of "gptbot"
        result = _find_matching_group(parsed, "gptbot")
        assert result is group


# ---------------------------------------------------------------------------
# Path permission tests
# ---------------------------------------------------------------------------


class TestIsPathAllowed:
    def test_simple_allow_all(self):
        """User-agent: * / Disallow: (empty) → allow everything."""
        parsed = parse_robots_txt("User-agent: *\nDisallow:\n")
        assert is_path_allowed(parsed, "/anything") is True
        assert is_path_allowed(parsed, "/") is True

    def test_block_private(self):
        """Disallow: /private/ blocks /private/ subtree."""
        parsed = parse_robots_txt("User-agent: *\nDisallow: /private/\n")
        assert is_path_allowed(parsed, "/private/report.html") is False
        assert is_path_allowed(parsed, "/blog/post") is True

    def test_allow_subset(self):
        """Specific bot blocked everywhere but allowed on /public-research/."""
        content = (
            "User-agent: ScanZechResearchBot\n"
            "Disallow: /\n"
            "Allow: /public-research/\n"
        )
        parsed = parse_robots_txt(content)
        assert is_path_allowed(parsed, "/public-research/paper1.html") is True
        assert is_path_allowed(parsed, "/admin/metrics") is False

    def test_ai_bots_blocked_blocks_us(self):
        """If GPTBot is blocked, we should also be blocked (ethical)."""
        content = (
            "User-agent: GPTBot\n"
            "Disallow: /\n\n"
            "User-agent: *\n"
            "Disallow:\n"
        )
        parsed = parse_robots_txt(content)
        # Even though * allows all, GPTBot is blocked so we block too
        assert is_path_allowed(parsed, "/blog/post") is False

    def test_our_bot_explicitly_blocked(self):
        """Our specific bot is blocked."""
        content = (
            "User-agent: scan-zech-sh-research-bot\n"
            "Disallow: /\n\n"
            "User-agent: *\n"
            "Disallow:\n"
        )
        parsed = parse_robots_txt(content)
        assert is_path_allowed(parsed, "/anything") is False

    def test_ai_input_no_blocks_all(self):
        """ai-input: no in comments blocks everything."""
        content = (
            "User-agent: *\n"
            "Disallow:\n"
            "# ai-input: no\n"
        )
        parsed = parse_robots_txt(content)
        assert is_path_allowed(parsed, "/blog/post") is False

    def test_mixed_allow_disallow(self):
        """Mixed Allow/Disallow with path specificity."""
        content = (
            "User-agent: *\n"
            "Disallow: /account/\n"
            "Disallow: /admin/\n"
            "Allow: /\n"
        )
        parsed = parse_robots_txt(content)
        assert is_path_allowed(parsed, "/blog/post-1") is True
        assert is_path_allowed(parsed, "/account/profile") is False
        assert is_path_allowed(parsed, "/admin/logs") is False

    def test_claudebot_blocked(self):
        """ClaudeBot blocked → we're blocked too."""
        content = (
            "User-agent: ClaudeBot\n"
            "Disallow: /\n\n"
            "User-agent: *\n"
            "Disallow:\n"
        )
        parsed = parse_robots_txt(content)
        assert is_path_allowed(parsed, "/page") is False

    def test_google_extended_blocked(self):
        """Google-Extended blocked → we're blocked too."""
        content = (
            "User-agent: Google-Extended\n"
            "Disallow: /\n\n"
            "User-agent: *\n"
            "Disallow:\n"
        )
        parsed = parse_robots_txt(content)
        assert is_path_allowed(parsed, "/page") is False

    def test_no_groups_allows_all(self):
        """Empty robots.txt allows everything."""
        parsed = parse_robots_txt("")
        assert is_path_allowed(parsed, "/anything") is True


# ---------------------------------------------------------------------------
# AI blocking detection
# ---------------------------------------------------------------------------


class TestIsAiBlocked:
    def test_not_blocked(self):
        parsed = parse_robots_txt("User-agent: *\nDisallow:\n")
        assert _is_ai_blocked(parsed) is False

    def test_gptbot_blocked(self):
        content = (
            "User-agent: GPTBot\nDisallow: /\n\n"
            "User-agent: *\nDisallow:\n"
        )
        parsed = parse_robots_txt(content)
        assert _is_ai_blocked(parsed) is True

    def test_ai_input_no(self):
        content = "User-agent: *\nDisallow:\n# ai-input: no\n"
        parsed = parse_robots_txt(content)
        assert _is_ai_blocked(parsed) is True


# ---------------------------------------------------------------------------
# Crawl delay tests
# ---------------------------------------------------------------------------


class TestGetCrawlDelay:
    def test_no_delay(self):
        parsed = parse_robots_txt("User-agent: *\nDisallow:\n")
        assert get_crawl_delay(parsed) is None

    def test_global_delay(self):
        parsed = parse_robots_txt(
            "User-agent: *\nCrawl-delay: 5\nDisallow:\n"
        )
        assert get_crawl_delay(parsed) == 5.0

    def test_most_restrictive_delay(self):
        content = (
            "User-agent: GPTBot\nCrawl-delay: 10\nDisallow:\n\n"
            "User-agent: *\nCrawl-delay: 2\nDisallow:\n"
        )
        parsed = parse_robots_txt(content)
        assert get_crawl_delay(parsed) == 10.0
