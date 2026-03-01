"""Tests for the ResearchPipeline component.

These tests verify that ResearchPipeline can be instantiated with a mock
dispatch, run independently, and that all events flow through the dispatch
callable.  External dependencies (Gemini API, Brave Search, Jina) are
patched so the tests run fast and offline.
"""

from __future__ import annotations

import asyncio
import sys
import os
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub out heavy framework and SDK dependencies so the test can import
# controllers.deep_research_agent without the full app stack installed.
_STUB_PREFIXES = (
    "skrift", "litestar", "sqlalchemy",
    "google.genai", "google.auth", "google.oauth2",
    "pydantic_ai",
    "controllers.brave_search", "controllers.domain_throttle",
    "controllers.llm", "controllers.robots",
)
_STUB_EXACT = (
    "httpx", "redis", "redis.asyncio",
    "bs4", "pypdf",
)

for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _STUB_PREFIXES):
        sys.modules[_mod_name] = MagicMock()
for _mod_name in _STUB_EXACT:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()
# Add sub-modules that are imported explicitly
for _mod_name in (
    "skrift", "skrift.app_factory", "skrift.auth", "skrift.auth.session_keys",
    "skrift.config", "skrift.db", "skrift.db.models", "skrift.db.models.user",
    "skrift.lib", "skrift.lib.notifications",
    "litestar", "litestar.response",
    "sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio",
    "sqlalchemy.sql", "sqlalchemy.sql.expression", "sqlalchemy.orm",
    "google.genai", "google.genai.types",
    "pydantic_ai", "pydantic_ai.messages", "pydantic_ai.usage",
    "controllers.brave_search", "controllers.domain_throttle",
    "controllers.llm", "controllers.robots",
):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# Ensure controllers.__init__ doesn't crash on the skrift monkeypatch
_skrift_mock = sys.modules["skrift.app_factory"]
_skrift_mock.render_markdown = lambda content: content

# Provide real Pydantic BaseModel since the module uses it for type definitions
import pydantic
sys.modules["pydantic"] = pydantic

from controllers.deep_research_agent import (
    CONFIG,
    GROUNDED_CONFIG,
    GROUNDED_PLANNING_PROMPT,
    LIGHT_CONFIG,
    LITE_GROUNDED_CONFIG,
    CostBudget,
    DetailEvent,
    Dispatch,
    DoneEvent,
    ErrorEvent,
    GroundedResearchPipeline,
    KnowledgeState,
    PipelineEvent,
    ResearchPipeline,
    StageEvent,
    TextEvent,
    TokenCounter,
    TopicPlan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EventCollector:
    """A mock dispatch that collects all emitted events."""

    def __init__(self) -> None:
        self.events: list[PipelineEvent] = []

    async def __call__(self, event: PipelineEvent) -> None:
        self.events.append(event)

    def of_type(self, cls: type) -> list:
        return [e for e in self.events if isinstance(e, cls)]

    @property
    def stages(self) -> list[str]:
        return [e.stage for e in self.of_type(StageEvent)]

    @property
    def detail_types(self) -> list[str]:
        return [e.type for e in self.of_type(DetailEvent)]

    @property
    def text(self) -> str:
        return "".join(e.text for e in self.of_type(TextEvent))


def _make_pipeline(
    dispatch: Dispatch,
    query: str = "What is TCP?",
    config: dict | None = None,
) -> ResearchPipeline:
    """Create a pipeline with minimal config for testing."""
    cfg = dict(LIGHT_CONFIG)
    cfg["max_topics"] = 2
    cfg["max_topic_sources"] = 2
    cfg["max_spawned_topics"] = 0
    cfg["max_total_topics"] = 2
    cfg["max_iterations"] = 1
    cfg["research_budget"] = 0.01
    if config:
        cfg.update(config)
    return ResearchPipeline(
        query,
        dispatch,
        brave_api_key="test-key",
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestResearchPipelineInit:
    """Test that ResearchPipeline initialises cleanly."""

    def test_default_config(self):
        collector = EventCollector()
        pipeline = ResearchPipeline(
            "test query",
            collector,
            brave_api_key="key",
        )
        assert pipeline.query == "test query"
        assert pipeline.config is CONFIG
        assert pipeline.brave_api_key == "key"
        assert isinstance(pipeline.knowledge, KnowledgeState)
        assert isinstance(pipeline.budget, CostBudget)

    def test_custom_config(self):
        collector = EventCollector()
        cfg = dict(LIGHT_CONFIG)
        cfg["research_budget"] = 0.01
        pipeline = ResearchPipeline(
            "test", collector, brave_api_key="k", config=cfg,
        )
        assert pipeline.config["research_budget"] == 0.01
        assert pipeline.budget.limit == 0.01

    def test_dispatch_is_stored(self):
        collector = EventCollector()
        pipeline = ResearchPipeline("q", collector, brave_api_key="k")
        assert pipeline.dispatch is collector

    def test_state_is_fresh(self):
        collector = EventCollector()
        p1 = ResearchPipeline("q1", collector, brave_api_key="k")
        p2 = ResearchPipeline("q2", collector, brave_api_key="k")
        # Each pipeline has independent state
        assert p1.knowledge is not p2.knowledge
        assert p1.budget is not p2.budget
        assert p1.already_fetched is not p2.already_fetched


class TestBuildFullQuery:
    """Test the query building logic."""

    def test_basic_query(self):
        collector = EventCollector()
        pipeline = ResearchPipeline(
            "What is TCP?", collector, brave_api_key="k",
        )
        full = pipeline._build_full_query()
        assert "What is TCP?" in full
        assert "Current date/time:" in full

    def test_with_timezone(self):
        collector = EventCollector()
        pipeline = ResearchPipeline(
            "test", collector, brave_api_key="k",
            user_timezone="US/Eastern",
        )
        full = pipeline._build_full_query()
        assert "US/Eastern" in full

    def test_with_conversation_history(self):
        collector = EventCollector()
        pipeline = ResearchPipeline(
            "follow-up question", collector, brave_api_key="k",
            conversation_history=[
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
            ],
        )
        full = pipeline._build_full_query()
        assert "Previous conversation:" in full
        assert "User: first question" in full
        assert "Assistant: first answer" in full
        assert "follow-up question" in full

    def test_invalid_timezone_falls_back(self):
        collector = EventCollector()
        pipeline = ResearchPipeline(
            "test", collector, brave_api_key="k",
            user_timezone="Invalid/Zone",
        )
        full = pipeline._build_full_query()
        assert "Invalid/Zone" in full  # Still included in the label


class TestDispatchCallable:
    """Test that different dispatch callables work."""

    @pytest.mark.asyncio
    async def test_async_function_dispatch(self):
        events = []

        async def my_dispatch(event: PipelineEvent) -> None:
            events.append(event)

        pipeline = _make_pipeline(my_dispatch)
        # Just verify the dispatch is callable
        await pipeline.dispatch(StageEvent(stage="reasoning"))
        assert len(events) == 1
        assert isinstance(events[0], StageEvent)

    @pytest.mark.asyncio
    async def test_queue_based_dispatch(self):
        """Verify asyncio.Queue.put works as a dispatch."""
        queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()
        pipeline = _make_pipeline(queue.put)
        await pipeline.dispatch(TextEvent(text="hello"))
        event = queue.get_nowait()
        assert isinstance(event, TextEvent)
        assert event.text == "hello"

    @pytest.mark.asyncio
    async def test_collector_dispatch(self):
        collector = EventCollector()
        pipeline = _make_pipeline(collector)
        await pipeline.dispatch(DoneEvent())
        assert len(collector.events) == 1


class TestPipelineRun:
    """Test the full pipeline run with mocked LLM/search."""

    @pytest.mark.asyncio
    async def test_run_emits_stages_and_done(self, monkeypatch):
        """Pipeline should emit reasoning, researching stages and end with done."""
        collector = EventCollector()
        pipeline = _make_pipeline(collector)

        # Mock _plan to return a single topic
        async def mock_plan(full_query, raw_query, cfg, dispatch, budget,
                            planning_counter, planning_prompt=""):
            await dispatch(DetailEvent(
                type="reasoning", payload={"text": "Planning..."},
            ))
            return [TopicPlan(
                id="t1", label="TCP Basics",
                description="How TCP works",
                queries=["TCP protocol overview"],
            )]

        # Mock _reconsider to pass through
        async def mock_reconsider(raw_query, topics, cfg, budget, counter, dispatch=None, output_format="topics"):
            return topics

        # Mock _research_topic to emit search events
        async def mock_research_topic(
            topic, knowledge, brave_api_key, already_fetched,
            queries_searched, cfg, dispatch, extraction_counter, budget,
            redis_url="", db_session=None,
        ):
            from controllers.deep_research_agent import TopicResult
            await dispatch(DetailEvent(
                type="research", payload={"topic": topic.label},
            ))
            await dispatch(DetailEvent(
                type="search",
                payload={"topic": topic.label, "query": "TCP protocol overview"},
            ))
            await dispatch(DetailEvent(
                type="search_done",
                payload={"topic": topic.label, "query": "TCP protocol overview", "num_results": 3},
            ))
            await dispatch(DetailEvent(
                type="result",
                payload={"topic": topic.label, "urls": [], "num_sources": 0},
            ))
            return TopicResult(topic_id=topic.id, entries_added=0)

        # Mock _articulate to emit text
        async def mock_articulate(query, knowledge, cfg, dispatch, counter):
            await dispatch(TextEvent(text="TCP is a "))
            await dispatch(TextEvent(text="transport protocol."))

        monkeypatch.setattr(
            "controllers.deep_research_agent._plan", mock_plan,
        )
        monkeypatch.setattr(
            "controllers.deep_research_agent._reconsider", mock_reconsider,
        )
        monkeypatch.setattr(
            "controllers.deep_research_agent._research_topic", mock_research_topic,
        )
        monkeypatch.setattr(
            "controllers.deep_research_agent._articulate", mock_articulate,
        )

        await pipeline.run()

        # Check stage events
        assert "reasoning" in collector.stages
        assert "researching" in collector.stages

        # Check detail events
        assert "reasoning" in collector.detail_types
        assert "research" in collector.detail_types
        assert "search" in collector.detail_types
        assert "search_done" in collector.detail_types
        assert "result" in collector.detail_types
        assert "usage" in collector.detail_types

        # Check text output
        assert collector.text == "TCP is a transport protocol."

        # Check done event
        assert len(collector.of_type(DoneEvent)) == 1
        assert len(collector.of_type(ErrorEvent)) == 0

    @pytest.mark.asyncio
    async def test_run_emits_error_on_plan_failure(self, monkeypatch):
        """If planning raises, pipeline should emit ErrorEvent."""
        collector = EventCollector()
        pipeline = _make_pipeline(collector)

        async def mock_plan_fail(*args, **kwargs):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(
            "controllers.deep_research_agent._plan", mock_plan_fail,
        )

        await pipeline.run()

        assert len(collector.of_type(ErrorEvent)) == 1
        assert "LLM unavailable" in collector.of_type(ErrorEvent)[0].error
        # Should still have the initial reasoning stage
        assert "reasoning" in collector.stages

    @pytest.mark.asyncio
    async def test_pipeline_is_reusable_pattern(self, monkeypatch):
        """Two pipelines with different dispatches get independent events."""
        collector1 = EventCollector()
        collector2 = EventCollector()
        pipeline1 = _make_pipeline(collector1, query="query 1")
        pipeline2 = _make_pipeline(collector2, query="query 2")

        async def mock_plan(full_query, raw_query, cfg, dispatch, budget,
                            planning_counter, planning_prompt=""):
            return [TopicPlan(
                id="t1", label="Topic",
                description="desc", queries=["q"],
            )]

        async def mock_reconsider(raw_query, topics, cfg, budget, counter, dispatch=None, output_format="topics"):
            return topics

        async def mock_research_topic(*args, **kwargs):
            from controllers.deep_research_agent import TopicResult
            return TopicResult(topic_id="t1", entries_added=0)

        async def mock_articulate(query, knowledge, cfg, dispatch, counter):
            # Emit different text based on which pipeline
            text = "result 1" if "query 1" in query else "result 2"
            await dispatch(TextEvent(text=text))

        monkeypatch.setattr("controllers.deep_research_agent._plan_survey", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._plan", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._reconsider", mock_reconsider)
        monkeypatch.setattr("controllers.deep_research_agent._research_topic", mock_research_topic)
        monkeypatch.setattr("controllers.deep_research_agent._articulate", mock_articulate)

        await pipeline1.run()
        await pipeline2.run()

        assert collector1.text == "result 1"
        assert collector2.text == "result 2"
        # Independent event streams
        assert len(collector1.events) > 0
        assert len(collector2.events) > 0


class TestPipelineSwappable:
    """Test that the pipeline dispatch can be swapped dynamically."""

    @pytest.mark.asyncio
    async def test_inline_async_dispatch(self):
        """A thin async wrapper can be used as dispatch."""
        events: list[PipelineEvent] = []

        async def append_dispatch(event: PipelineEvent) -> None:
            events.append(event)

        pipeline = _make_pipeline(append_dispatch)
        await pipeline.dispatch(StageEvent(stage="reasoning"))
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_class_method_dispatch(self):
        """A class method can be used as dispatch."""

        class MyHandler:
            def __init__(self):
                self.events = []

            async def handle(self, event: PipelineEvent) -> None:
                self.events.append(event)

        handler = MyHandler()
        pipeline = _make_pipeline(handler.handle)
        await pipeline.dispatch(TextEvent(text="hello"))
        assert len(handler.events) == 1
        assert handler.events[0].text == "hello"


# ---------------------------------------------------------------------------
# Grounded pipeline helpers
# ---------------------------------------------------------------------------


def _make_grounded_pipeline(
    dispatch: Dispatch,
    query: str = "What is TCP?",
    config: dict | None = None,
) -> GroundedResearchPipeline:
    """Create a grounded pipeline with minimal config for testing."""
    cfg = dict(GROUNDED_CONFIG)
    cfg["max_topics"] = 2
    cfg["max_topic_sources"] = 2
    cfg["max_spawned_topics"] = 0
    cfg["max_total_topics"] = 2
    cfg["research_budget"] = 0.01
    cfg["shallow_budget_fraction"] = 0.30
    cfg["evaluate_max_topics"] = 4
    if config:
        cfg.update(config)
    return GroundedResearchPipeline(
        query,
        dispatch,
        brave_api_key="test-key",
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Grounded pipeline tests
# ---------------------------------------------------------------------------


class TestGroundedPipelineInit:
    """Test that GroundedResearchPipeline initialises cleanly."""

    def test_default_config(self):
        collector = EventCollector()
        pipeline = GroundedResearchPipeline(
            "test query",
            collector,
            brave_api_key="key",
        )
        assert pipeline.query == "test query"
        assert pipeline.config is GROUNDED_CONFIG
        assert pipeline.planning_prompt == GROUNDED_PLANNING_PROMPT
        assert isinstance(pipeline.knowledge, KnowledgeState)
        assert isinstance(pipeline.budget, CostBudget)

    def test_lite_config(self):
        collector = EventCollector()
        pipeline = GroundedResearchPipeline(
            "test", collector, brave_api_key="k",
            config=LITE_GROUNDED_CONFIG,
        )
        assert pipeline.config is LITE_GROUNDED_CONFIG
        assert pipeline.config["research_budget"] == 0.15
        assert pipeline.config["max_topics"] == 3
        assert pipeline.config["shallow_jina_reads"] == 2
        assert pipeline.config["evaluate_max_topics"] == 5
        assert pipeline.config["max_topic_sources"] == 4
        assert pipeline.config["max_spawned_topics"] == 0
        assert pipeline.budget.limit == 0.15

    def test_dispatch_is_stored(self):
        collector = EventCollector()
        pipeline = GroundedResearchPipeline("q", collector, brave_api_key="k")
        assert pipeline.dispatch is collector

    def test_state_is_fresh(self):
        collector = EventCollector()
        p1 = GroundedResearchPipeline("q1", collector, brave_api_key="k")
        p2 = GroundedResearchPipeline("q2", collector, brave_api_key="k")
        assert p1.knowledge is not p2.knowledge
        assert p1.budget is not p2.budget
        assert p1.already_fetched is not p2.already_fetched


class TestGroundedPipelineRun:
    """Test the full grounded pipeline with mocked LLM/search."""

    @pytest.mark.asyncio
    async def test_run_emits_correct_stage_transitions(self, monkeypatch):
        """Grounded pipeline should emit reasoning → researching → reasoning → researching → done."""
        collector = EventCollector()
        pipeline = _make_grounded_pipeline(collector)

        # Mock _plan (conservative planning)
        async def mock_plan(full_query, raw_query, cfg, dispatch, budget,
                            planning_counter, planning_prompt=""):
            await dispatch(DetailEvent(
                type="reasoning", payload={"text": "Conservative plan..."},
            ))
            return [TopicPlan(
                id="t1", label="TCP Overview",
                description="Basic TCP understanding",
                queries=["TCP protocol basics"],
            )]

        # Mock _reconsider to pass through
        async def mock_reconsider(raw_query, topics, cfg, budget, counter, dispatch=None, output_format="topics"):
            return topics

        # Mock _search_and_extract_query for shallow research
        async def mock_search_extract(
            query_text, topic, knowledge, brave_api_key,
            already_fetched, cfg, dispatch, extraction_counter,
            budget, topic_entries, redis_url="", db_session=None,
        ):
            from controllers.deep_research_agent import KnowledgeEntry
            entry = KnowledgeEntry(
                source_id="s1", url="https://example.com",
                title="TCP Guide", query=query_text,
                key_points="TCP is a transport protocol.",
                char_count=30, topic=topic.label,
            )
            knowledge.add(entry)
            topic_entries.append(entry)
            await dispatch(DetailEvent(
                type="search",
                payload={"topic": topic.label, "query": query_text},
            ))
            await dispatch(DetailEvent(
                type="result",
                payload={"topic": topic.label, "urls": ["https://example.com"], "num_sources": 1},
            ))

        # Mock _evaluate_shallow_research
        async def mock_evaluate(raw_query, knowledge, cfg, dispatch, budget, counter):
            await dispatch(DetailEvent(
                type="reasoning", payload={"text": "Based on findings..."},
            ))
            return [TopicPlan(
                id="t1", label="TCP Internals",
                description="Deeper TCP mechanics",
                queries=["TCP congestion control"],
            )]

        # Mock _research_topic for deep phase
        async def mock_research_topic(
            topic, knowledge, brave_api_key, already_fetched,
            queries_searched, cfg, dispatch, extraction_counter, budget,
            redis_url="", db_session=None,
        ):
            from controllers.deep_research_agent import TopicResult
            await dispatch(DetailEvent(
                type="research", payload={"topic": topic.label},
            ))
            return TopicResult(topic_id=topic.id, entries_added=0)

        # Mock _articulate
        async def mock_articulate(query, knowledge, cfg, dispatch, counter):
            await dispatch(TextEvent(text="TCP uses congestion control."))

        monkeypatch.setattr("controllers.deep_research_agent._plan_survey", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._plan", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._reconsider", mock_reconsider)
        monkeypatch.setattr("controllers.deep_research_agent._search_and_extract_query", mock_search_extract)
        monkeypatch.setattr("controllers.deep_research_agent._evaluate_shallow_research", mock_evaluate)
        monkeypatch.setattr("controllers.deep_research_agent._research_topic", mock_research_topic)
        monkeypatch.setattr("controllers.deep_research_agent._articulate", mock_articulate)

        await pipeline.run()

        # Verify stage transitions: reasoning → researching → reasoning → researching
        assert collector.stages == ["reasoning", "researching", "reasoning", "researching"]

        # Verify done and no errors
        assert len(collector.of_type(DoneEvent)) == 1
        assert len(collector.of_type(ErrorEvent)) == 0

        # Verify text output
        assert collector.text == "TCP uses congestion control."

        # Verify usage event
        assert "usage" in collector.detail_types

    @pytest.mark.asyncio
    async def test_shallow_research_uses_reduced_config(self, monkeypatch):
        """Shallow research should use shallow_brave_results and shallow_jina_reads."""
        collector = EventCollector()
        pipeline = _make_grounded_pipeline(collector)

        captured_cfgs: list[dict] = []

        async def mock_plan(full_query, raw_query, cfg, dispatch, budget,
                            planning_counter, planning_prompt=""):
            return [TopicPlan(
                id="t1", label="Topic",
                description="desc", queries=["query1"],
            )]

        async def mock_reconsider(raw_query, topics, cfg, budget, counter, dispatch=None, output_format="topics"):
            return topics

        async def mock_search_extract(
            query_text, topic, knowledge, brave_api_key,
            already_fetched, cfg, dispatch, extraction_counter,
            budget, topic_entries, redis_url="", db_session=None,
        ):
            # Capture the config passed to shallow research
            captured_cfgs.append(dict(cfg))

        async def mock_evaluate(raw_query, knowledge, cfg, dispatch, budget, counter):
            return [TopicPlan(id="t1", label="Deep", description="d", queries=["dq"])]

        async def mock_research_topic(*args, **kwargs):
            from controllers.deep_research_agent import TopicResult
            return TopicResult(topic_id="t1", entries_added=0)

        async def mock_articulate(query, knowledge, cfg, dispatch, counter):
            await dispatch(TextEvent(text="done"))

        monkeypatch.setattr("controllers.deep_research_agent._plan_survey", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._plan", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._reconsider", mock_reconsider)
        monkeypatch.setattr("controllers.deep_research_agent._search_and_extract_query", mock_search_extract)
        monkeypatch.setattr("controllers.deep_research_agent._evaluate_shallow_research", mock_evaluate)
        monkeypatch.setattr("controllers.deep_research_agent._research_topic", mock_research_topic)
        monkeypatch.setattr("controllers.deep_research_agent._articulate", mock_articulate)

        await pipeline.run()

        # The shallow phase should have called _search_and_extract_query with reduced config
        assert len(captured_cfgs) >= 1
        shallow_cfg = captured_cfgs[0]
        assert shallow_cfg["brave_results"] == GROUNDED_CONFIG["shallow_brave_results"]
        assert shallow_cfg["jina_reads"] == GROUNDED_CONFIG["shallow_jina_reads"]

    @pytest.mark.asyncio
    async def test_evaluate_receives_populated_knowledge(self, monkeypatch):
        """Evaluate should receive knowledge populated by shallow research."""
        collector = EventCollector()
        pipeline = _make_grounded_pipeline(collector)

        evaluate_knowledge_entries: list[int] = []

        async def mock_plan(full_query, raw_query, cfg, dispatch, budget,
                            planning_counter, planning_prompt=""):
            return [TopicPlan(
                id="t1", label="Topic",
                description="desc", queries=["q1"],
            )]

        async def mock_reconsider(raw_query, topics, cfg, budget, counter, dispatch=None, output_format="topics"):
            return topics

        async def mock_search_extract(
            query_text, topic, knowledge, brave_api_key,
            already_fetched, cfg, dispatch, extraction_counter,
            budget, topic_entries, redis_url="", db_session=None,
        ):
            from controllers.deep_research_agent import KnowledgeEntry
            entry = KnowledgeEntry(
                source_id="s1", url="https://example.com",
                title="Source", query=query_text,
                key_points="Some findings.",
                char_count=15, topic=topic.label,
            )
            knowledge.add(entry)
            topic_entries.append(entry)

        async def mock_evaluate(raw_query, knowledge, cfg, dispatch, budget, counter):
            evaluate_knowledge_entries.append(len(knowledge.entries))
            return [TopicPlan(id="t1", label="Deep", description="d", queries=["dq"])]

        async def mock_research_topic(*args, **kwargs):
            from controllers.deep_research_agent import TopicResult
            return TopicResult(topic_id="t1", entries_added=0)

        async def mock_articulate(query, knowledge, cfg, dispatch, counter):
            await dispatch(TextEvent(text="done"))

        monkeypatch.setattr("controllers.deep_research_agent._plan_survey", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._plan", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._reconsider", mock_reconsider)
        monkeypatch.setattr("controllers.deep_research_agent._search_and_extract_query", mock_search_extract)
        monkeypatch.setattr("controllers.deep_research_agent._evaluate_shallow_research", mock_evaluate)
        monkeypatch.setattr("controllers.deep_research_agent._research_topic", mock_research_topic)
        monkeypatch.setattr("controllers.deep_research_agent._articulate", mock_articulate)

        await pipeline.run()

        # Evaluate should have seen at least 1 entry from shallow research
        assert evaluate_knowledge_entries == [1]

    @pytest.mark.asyncio
    async def test_deep_iterate_uses_evaluate_topics(self, monkeypatch):
        """Deep research phase should use topics from evaluate, not from plan."""
        collector = EventCollector()
        pipeline = _make_grounded_pipeline(collector)

        deep_topic_labels: list[str] = []

        async def mock_plan(full_query, raw_query, cfg, dispatch, budget,
                            planning_counter, planning_prompt=""):
            return [TopicPlan(
                id="t1", label="Plan Topic",
                description="from plan", queries=["pq"],
            )]

        async def mock_reconsider(raw_query, topics, cfg, budget, counter, dispatch=None, output_format="topics"):
            return topics

        async def mock_search_extract(
            query_text, topic, knowledge, brave_api_key,
            already_fetched, cfg, dispatch, extraction_counter,
            budget, topic_entries, redis_url="", db_session=None,
        ):
            pass

        async def mock_evaluate(raw_query, knowledge, cfg, dispatch, budget, counter):
            return [
                TopicPlan(id="t1", label="Evaluate Topic A", description="from eval", queries=["eq1"]),
                TopicPlan(id="t2", label="Evaluate Topic B", description="from eval", queries=["eq2"]),
            ]

        async def mock_research_topic(
            topic, knowledge, brave_api_key, already_fetched,
            queries_searched, cfg, dispatch, extraction_counter, budget,
            redis_url="", db_session=None,
        ):
            from controllers.deep_research_agent import TopicResult
            deep_topic_labels.append(topic.label)
            return TopicResult(topic_id=topic.id, entries_added=0)

        async def mock_articulate(query, knowledge, cfg, dispatch, counter):
            await dispatch(TextEvent(text="done"))

        monkeypatch.setattr("controllers.deep_research_agent._plan_survey", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._plan", mock_plan)
        monkeypatch.setattr("controllers.deep_research_agent._reconsider", mock_reconsider)
        monkeypatch.setattr("controllers.deep_research_agent._search_and_extract_query", mock_search_extract)
        monkeypatch.setattr("controllers.deep_research_agent._evaluate_shallow_research", mock_evaluate)
        monkeypatch.setattr("controllers.deep_research_agent._research_topic", mock_research_topic)
        monkeypatch.setattr("controllers.deep_research_agent._articulate", mock_articulate)

        await pipeline.run()

        # Deep phase should use evaluate topics, not plan topics
        assert "Evaluate Topic A" in deep_topic_labels
        assert "Evaluate Topic B" in deep_topic_labels
        assert "Plan Topic" not in deep_topic_labels

    @pytest.mark.asyncio
    async def test_error_propagation(self, monkeypatch):
        """If planning raises, grounded pipeline should emit ErrorEvent."""
        collector = EventCollector()
        pipeline = _make_grounded_pipeline(collector)

        async def mock_plan_fail(*args, **kwargs):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(
            "controllers.deep_research_agent._plan_survey", mock_plan_fail,
        )
        monkeypatch.setattr(
            "controllers.deep_research_agent._plan", mock_plan_fail,
        )

        await pipeline.run()

        assert len(collector.of_type(ErrorEvent)) == 1
        assert "LLM unavailable" in collector.of_type(ErrorEvent)[0].error
        assert "reasoning" in collector.stages
