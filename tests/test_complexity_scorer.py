"""Tests for the deterministic prompt router."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityLevel, ComplexityScorer


@pytest.fixture
def scorer():
    return ComplexityScorer()


def run(coro):
    return asyncio.run(coro)


# -----------------------------------------------------------------------
# Hard-match: Simple
# -----------------------------------------------------------------------

class TestSimplePatterns:
    @pytest.mark.parametrize("prompt", [
        "hi", "Hello!", "hey", "thanks", "ok", "yes", "no",
        "bye", "lol", "nice one", "yep", "nah",
    ])
    def test_greetings_and_acks(self, scorer, prompt):
        r = run(scorer.score_complexity(prompt))
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
        r = run(scorer.score_complexity(prompt))
        assert r.classification == ComplexityLevel.COMPLEX


# -----------------------------------------------------------------------
# Score-based routing
# -----------------------------------------------------------------------

class TestScoreRouting:
    def test_short_code_cue_at_least_moderate(self, scorer):
        r = run(scorer.score_complexity("What does len do in Python?"))
        assert r.classification in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX)

    def test_short_domain_term_at_least_moderate(self, scorer):
        r = run(scorer.score_complexity("qubit?"))
        assert r.classification in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX)

    def test_fix_this_regex_not_simple(self, scorer):
        r = run(scorer.score_complexity("fix this regex"))
        assert r.classification != ComplexityLevel.SIMPLE

    def test_simple_question_no_cues(self, scorer):
        r = run(scorer.score_complexity("what time is it?"))
        assert r.classification == ComplexityLevel.SIMPLE

    def test_long_detailed_prompt_is_complex(self, scorer):
        prompt = (
            "explain the difference between supervised and unsupervised machine learning, "
            "including trade-offs, use cases, and how gradient descent works in each"
        )
        r = run(scorer.score_complexity(prompt))
        assert r.classification == ComplexityLevel.COMPLEX


# -----------------------------------------------------------------------
# Follow-up context inheritance
# -----------------------------------------------------------------------

class TestFollowUp:
    def test_explain_more_inherits_prior(self, scorer):
        messages = [
            {"role": "user", "content": "explain how neural networks do backpropagation"},
            {"role": "assistant", "content": "Backpropagation works by..."},
        ]
        r = run(scorer.score_complexity("explain more", messages=messages))
        assert r.classification != ComplexityLevel.SIMPLE

    def test_bare_why_inherits_context(self, scorer):
        messages = [
            {"role": "user", "content": "implement a binary search tree"},
            {"role": "assistant", "content": "Here's the implementation..."},
        ]
        r = run(scorer.score_complexity("why?", messages=messages))
        assert r.classification != ComplexityLevel.SIMPLE
