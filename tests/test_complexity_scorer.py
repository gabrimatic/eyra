"""Tests for the deterministic prompt router."""

import asyncio
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityScorer, ComplexityLevel


@pytest.fixture
def scorer():
    return ComplexityScorer()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# -----------------------------------------------------------------------
# Hard-match: Simple
# -----------------------------------------------------------------------

class TestSimplePatterns:
    @pytest.mark.parametrize("prompt", [
        "hi", "Hello!", "hey", "thanks", "ok", "yes", "no",
        "bye", "lol", "nice one", "yep", "nah",
    ])
    def test_greetings_and_acks(self, scorer, prompt):
        r = run(scorer.score_complexity(prompt, "text"))
        assert r.classification == ComplexityLevel.SIMPLE


# -----------------------------------------------------------------------
# Hard-match: Complex
# -----------------------------------------------------------------------

class TestComplexPatterns:
    @pytest.mark.parametrize("prompt", [
        "implement a function to parse CSV files",
        "write a class for database connection pooling",
        "debug the bug in the authentication module",
        "refactor the code in the API layer",
        "compare actor-critic and pure policy gradient methods",
        "design a system for real-time event processing",
    ])
    def test_complex_hard_match(self, scorer, prompt):
        r = run(scorer.score_complexity(prompt, "text"))
        assert r.classification == ComplexityLevel.COMPLEX


# -----------------------------------------------------------------------
# Score-based routing
# -----------------------------------------------------------------------

class TestScoreRouting:
    def test_short_code_cue_at_least_moderate(self, scorer):
        r = run(scorer.score_complexity("What does len do in Python?", "text"))
        assert r.classification in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX)

    def test_short_domain_term_at_least_moderate(self, scorer):
        r = run(scorer.score_complexity("qubit?", "text"))
        assert r.classification in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX)

    def test_fix_this_regex_not_simple(self, scorer):
        r = run(scorer.score_complexity("fix this regex", "text"))
        assert r.classification != ComplexityLevel.SIMPLE

    def test_simple_question_no_cues(self, scorer):
        r = run(scorer.score_complexity("what time is it?", "text"))
        assert r.classification == ComplexityLevel.SIMPLE

    def test_long_detailed_prompt_is_complex(self, scorer):
        prompt = (
            "explain the difference between supervised and unsupervised machine learning, "
            "including trade-offs, use cases, and how gradient descent works in each"
        )
        r = run(scorer.score_complexity(prompt, "text"))
        assert r.classification == ComplexityLevel.COMPLEX


# -----------------------------------------------------------------------
# Image routing
# -----------------------------------------------------------------------

class TestImageRouting:
    def test_bare_image_is_moderate(self, scorer):
        r = run(scorer.score_complexity("#image", "image"))
        assert r.classification == ComplexityLevel.MODERATE

    def test_describe_image_is_moderate(self, scorer):
        r = run(scorer.score_complexity("describe what you see", "image"))
        assert r.classification == ComplexityLevel.MODERATE

    def test_extract_text_is_complex(self, scorer):
        r = run(scorer.score_complexity("extract all text from the screenshot", "image"))
        assert r.classification == ComplexityLevel.COMPLEX

    def test_find_bug_is_complex(self, scorer):
        r = run(scorer.score_complexity("find the bug in this screenshot", "image"))
        assert r.classification == ComplexityLevel.COMPLEX

    def test_image_never_simple(self, scorer):
        r = run(scorer.score_complexity("hi", "image"))
        assert r.classification != ComplexityLevel.SIMPLE


# -----------------------------------------------------------------------
# Follow-up context inheritance
# -----------------------------------------------------------------------

class TestFollowUp:
    def test_explain_more_inherits_prior(self, scorer):
        messages = [
            {"role": "user", "content": "explain how neural networks do backpropagation"},
            {"role": "assistant", "content": "Backpropagation works by..."},
        ]
        r = run(scorer.score_complexity("explain more", "text", messages=messages))
        assert r.classification != ComplexityLevel.SIMPLE

    def test_bare_why_inherits_context(self, scorer):
        messages = [
            {"role": "user", "content": "implement a binary search tree"},
            {"role": "assistant", "content": "Here's the implementation..."},
        ]
        r = run(scorer.score_complexity("why?", "text", messages=messages))
        assert r.classification != ComplexityLevel.SIMPLE
