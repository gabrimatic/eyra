"""
Deterministic prompt router for Eyra.

Routes user prompts to Simple, Moderate, or Complex models using
pattern matching and weighted signal scoring. No ML dependencies.
"""

import logging
import re
from enum import Enum
from typing import List, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types (unchanged interface)
# ---------------------------------------------------------------------------

class ComplexityLevel(str, Enum):
    SIMPLE = "Simple"
    MODERATE = "Moderate"
    COMPLEX = "Complex"


class ComplexityResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    classification: ComplexityLevel = Field(..., description="Complexity level.")
    confidence: float = Field(..., ge=0, le=1, description="Confidence [0..1]")


# ---------------------------------------------------------------------------
# Hard-match patterns
# ---------------------------------------------------------------------------

_SIMPLE_PATTERNS: list[re.Pattern] = [
    # Greetings
    re.compile(r"^(hi|hello|hey|yo|sup|howdy|hola|morning|evening|afternoon)[\s!?.]*$", re.I),
    # Acknowledgments and thanks
    re.compile(r"^(thanks?|thank\s*you|thx|ty|cheers|appreciated|cool|nice|great|awesome|perfect|ok(ay)?|sure|got\s*it|noted|done|right|yep|yup|nope|nah)[\s!?.]*$", re.I),
    # Yes / no
    re.compile(r"^(yes|no|yeah|nah|yep|yup|nope|y|n)[\s!?.]*$", re.I),
    # Farewells
    re.compile(r"^(bye|goodbye|see\s*ya|later|ciao|goodnight|gn)[\s!?.]*$", re.I),
    # Tiny social turns
    re.compile(r"^(lol|haha|heh|wow|omg|bruh|nice one|fair enough|same|true)[\s!?.]*$", re.I),
]

_COMPLEX_CODE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(implement|write|code|build|create)\b.{0,40}\b(function|class|module|script|program|api|endpoint|server|parser|compiler|handler|service|component|tree|queue|stack|graph|struct|iterator)\b", re.I),
    re.compile(r"\b(debug|fix|patch|troubleshoot|diagnose)\b.{0,30}\b(bug|error|issue|crash|exception|failure|segfault)\b", re.I),
    re.compile(r"\b(debug|fix|patch|rewrite|optimize)\b.{0,20}\b(this\s+)?(regex|query|sql|css|code|script|function|loop|algorithm)\b", re.I),
    re.compile(r"\b(review|audit|inspect)\b.{0,30}\b(code|pull\s*request|pr|commit|diff|security)\b", re.I),
    re.compile(r"\b(refactor|rewrite|optimize|redesign)\b.{0,30}\b(code|function|class|module|system|architecture)\b", re.I),
]

_COMPLEX_ANALYSIS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(compare|contrast|evaluate|analyze|assess)\b.{0,80}\b(approach|methods?|algorithms?|architecture|frameworks?|strategy|strategies|trade.?offs?)\b", re.I),
    re.compile(r"\b(design|architect|plan)\b.{0,30}\b(system|schema|database|pipeline|workflow|infrastructure)\b", re.I),
    re.compile(r"\b(prove|derive|formalize)\b", re.I),
    re.compile(r"\bmulti.?step\b|\bstep.?by.?step\b.{0,20}\b(plan|solution|approach)\b", re.I),
    re.compile(r"\b(expert|in.?depth|thorough|comprehensive|detailed)\b.{0,20}\b(analysis|review|assessment|explanation)\b", re.I),
]

_COMPLEX_IMAGE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(extract|ocr|read|transcribe)\b.{0,20}\b(text|code|content|data)\b", re.I),
    re.compile(r"\b(find|spot|identify|locate)\b.{0,20}\b(bug|error|issue|problem|mistake)\b", re.I),
    re.compile(r"\b(accessibility|a11y|wcag|ui\s*review|ux\s*audit)\b", re.I),
    re.compile(r"\b(deep|thorough|detailed|comprehensive)\b.{0,20}\b(analy|review|inspect|assess)", re.I),
    re.compile(r"\bcode\s*screenshot\b", re.I),
]

# ---------------------------------------------------------------------------
# Scoring cues (weighted signal lists)
# ---------------------------------------------------------------------------

_REASONING_CUES = [
    "why", "how does", "explain", "what causes", "reason", "because",
    "trade-off", "tradeoff", "pros and cons", "implications", "consequence",
    "assumption", "constraint", "caveat",
    "difference between", "differences between", "distinguish",
]

_CODE_DEBUG_CUES = [
    "function", "class", "method", "variable", "loop", "recursion",
    "algorithm", "regex", "sql", "query", "api", "endpoint", "http",
    "error", "bug", "stack trace", "exception", "crash", "lint",
    "test", "unit test", "integration", "deploy", "docker", "ci/cd",
    "git", "branch", "merge", "commit", "typescript", "python", "rust",
    "javascript", "java", "kotlin", "swift", "go ", "golang", "c++",
    "bash", "shell", "terminal", "command line",
]

_STRUCTURED_OUTPUT_CUES = [
    "table", "json", "yaml", "xml", "csv", "markdown", "format as",
    "schema", "template", "spec", "specification",
]

# Curated domain terms: ~80 high-signal exact phrases
_DOMAIN_TERMS = frozenset([
    # CS / programming
    "algorithm", "data structure", "time complexity", "space complexity",
    "dynamic programming", "recursion", "binary search", "hash map",
    "linked list", "tree traversal", "graph algorithm", "sorting",
    "concurrency", "mutex", "semaphore", "deadlock", "race condition",
    "design pattern", "dependency injection", "microservice",
    # ML / AI
    "machine learning", "deep learning", "neural network", "transformer",
    "gradient descent", "backpropagation", "loss function", "overfitting",
    "reinforcement learning", "attention mechanism", "fine-tuning",
    "embedding", "tokenizer", "inference", "training",
    # Math
    "linear algebra", "calculus", "differential equation", "probability",
    "statistics", "eigenvalue", "matrix", "fourier transform",
    "optimization", "convex", "stochastic",
    # Infra / DevOps
    "kubernetes", "docker", "ci/cd", "load balancer", "reverse proxy",
    "database", "replication", "sharding", "cache invalidation",
    "message queue", "event-driven", "serverless",
    # Security
    "encryption", "authentication", "authorization", "vulnerability",
    "sql injection", "xss", "csrf", "zero-day", "penetration testing",
    "cryptography", "certificate", "tls", "oauth",
    # Systems
    "operating system", "kernel", "memory management", "file system",
    "process", "thread", "scheduler", "virtual memory",
    "compiler", "interpreter", "garbage collection",
    # Physics / quantum
    "qubit", "quantum", "relativity", "superposition", "entanglement",
])

# Short referential follow-ups that should inherit prior context
_FOLLOWUP_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(explain|elaborate|tell me)\s*(more|further|again)[\s?.!]*$", re.I),
    re.compile(r"^(why|how|what)[\s?!.]*$", re.I),
    re.compile(r"^(continue|go on|keep going|more)[\s?.!]*$", re.I),
    re.compile(r"^(rewrite|redo|do)\s*(it|that|this)(.{0,20})$", re.I),
    re.compile(r"^(now\s+)?(in|using|with)\s+\w+[\s?.!]*$", re.I),
    re.compile(r"^(can you|could you|please)\s+(expand|clarify|rephrase)[\s?.!]*", re.I),
]


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class ComplexityScorer:
    """
    Deterministic prompt router. No ML models, no heavy dependencies.
    """

    def __init__(self):
        logger.info("ComplexityScorer (deterministic router) initialized.")

    async def score_complexity(
        self,
        text_content: Optional[str],
        task_type: str,
        image_base64: Optional[str] = None,
        messages: Optional[List[Dict]] = None,
    ) -> ComplexityResponse:
        text = (text_content or "").strip()

        # ----- Image routing -----
        if task_type == "image":
            result = self._route_image(text)
            logger.info(f"[Router] image -> {result.classification} (conf={result.confidence:.2f})")
            return result

        # ----- Text routing -----

        # 1. Hard-match simple
        if text and self._matches_any(text, _SIMPLE_PATTERNS):
            result = ComplexityResponse(classification=ComplexityLevel.SIMPLE, confidence=0.90)
            logger.info(f"[Router] hard-simple -> {result.classification}")
            return result

        # 2. Hard-match complex
        if text and self._matches_any(text, _COMPLEX_CODE_PATTERNS + _COMPLEX_ANALYSIS_PATTERNS):
            result = ComplexityResponse(classification=ComplexityLevel.COMPLEX, confidence=0.90)
            logger.info(f"[Router] hard-complex -> {result.classification}")
            return result

        # 3. Score-based routing
        score = self._compute_text_score(text, messages)
        result = self._classify_from_score(score)
        logger.info(f"[Router] score={score:.3f} -> {result.classification} (conf={result.confidence:.2f})")
        return result

    # ----- Image routing -----

    def _route_image(self, text: str) -> ComplexityResponse:
        """Image tasks never route to Simple."""
        if not text or text in ("#image", "#selfie"):
            return ComplexityResponse(classification=ComplexityLevel.MODERATE, confidence=0.90)

        # Generic descriptions
        generic = re.compile(r"^(describe|what is|what do you see|what's in|look at)\b", re.I)
        if generic.match(text) and not self._matches_any(text, _COMPLEX_IMAGE_PATTERNS):
            return ComplexityResponse(classification=ComplexityLevel.MODERATE, confidence=0.75)

        # Complex image analysis
        if self._matches_any(text, _COMPLEX_IMAGE_PATTERNS):
            return ComplexityResponse(classification=ComplexityLevel.COMPLEX, confidence=0.90)

        # Default for image: Moderate
        return ComplexityResponse(classification=ComplexityLevel.MODERATE, confidence=0.75)

    # ----- Text score computation -----

    def _compute_text_score(self, text: str, messages: Optional[List[Dict]]) -> float:
        if not text:
            return 0.0

        text_lower = text.lower()
        words = text_lower.split()
        score = 0.0

        # Reasoning cues (weight: 0.15 each, max ~0.45)
        reasoning_hits = sum(1 for cue in _REASONING_CUES if cue in text_lower)
        score += min(0.45, reasoning_hits * 0.15)

        # Code/debug cues (weight: 0.10 each, max ~0.40)
        code_hits = sum(1 for cue in _CODE_DEBUG_CUES if cue in text_lower)
        score += min(0.40, code_hits * 0.10)

        # Structured output cues (weight: 0.10 each, max ~0.20)
        struct_hits = sum(1 for cue in _STRUCTURED_OUTPUT_CUES if cue in text_lower)
        score += min(0.20, struct_hits * 0.10)

        # Domain terms (weight: 0.12 each, max ~0.36)
        domain_hits = sum(1 for term in _DOMAIN_TERMS if term in text_lower)
        score += min(0.36, domain_hits * 0.12)

        # Constraint count: sentences with "must", "should", "require", "need to", "ensure"
        constraint_words = ["must", "should", "require", "need to", "ensure", "make sure"]
        constraint_hits = sum(1 for c in constraint_words if c in text_lower)
        score += min(0.20, constraint_hits * 0.10)

        # Prompt length bonus (longer prompts tend toward complexity)
        if len(words) > 50:
            score += 0.15
        elif len(words) > 25:
            score += 0.08

        # Short prompt with code cues: ensure at least Moderate threshold
        if len(words) <= 8 and code_hits > 0:
            score = max(score, 0.30)

        # Very short prompt (1-3 words) with domain terms: ensure at least Moderate
        if len(words) <= 3 and domain_hits > 0:
            score = max(score, 0.30)

        # Question mark alone (short question) slight reduction,
        # but only if no domain terms or code cues were found
        if text.endswith("?") and len(words) <= 6 and score < 0.4 and domain_hits == 0 and code_hits == 0:
            score *= 0.7

        # Follow-up context inheritance
        if messages and self._is_followup(text):
            prior_level = self._get_prior_complexity(messages)
            if prior_level is not None:
                # Blend: use at least the prior level
                prior_score = {
                    ComplexityLevel.SIMPLE: 0.15,
                    ComplexityLevel.MODERATE: 0.45,
                    ComplexityLevel.COMPLEX: 0.75,
                }.get(prior_level, 0.0)
                score = max(score, prior_score)

        return min(1.0, score)

    def _is_followup(self, text: str) -> bool:
        """Check if text is a short referential follow-up."""
        if len(text.split()) > 8:
            return False
        return self._matches_any(text, _FOLLOWUP_PATTERNS)

    def _get_prior_complexity(self, messages: List[Dict]) -> Optional[ComplexityLevel]:
        """Look at the last 3 user messages for context."""
        user_msgs = [m for m in messages if m.get("role") == "user"]
        recent = user_msgs[-3:] if len(user_msgs) >= 3 else user_msgs

        if not recent:
            return None

        # Score the most recent substantial user message
        for msg in reversed(recent):
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    item.get("text", "") for item in content if item.get("type") == "text"
                )
            content = content.strip()
            if not content or self._is_followup(content):
                continue
            return self._classify_text_standalone(content)

        return None

    def _classify_text_standalone(self, text: str) -> ComplexityLevel:
        """Classify a text prompt using full routing logic (no follow-up recursion)."""
        if self._matches_any(text, _SIMPLE_PATTERNS):
            return ComplexityLevel.SIMPLE
        if self._matches_any(text, _COMPLEX_CODE_PATTERNS + _COMPLEX_ANALYSIS_PATTERNS):
            return ComplexityLevel.COMPLEX
        score = self._compute_text_score(text, messages=None)
        return self._classify_from_score(score).classification

    # ----- Classification from score -----

    def _classify_from_score(self, score: float) -> ComplexityResponse:
        """
        Thresholds:
          < 0.30 -> Simple
          < 0.60 -> Moderate
          >= 0.60 -> Complex

        Confidence:
          0.90 for hard-rule (handled upstream)
          0.75 for clear margin from thresholds
          0.60 for borderline
        """
        if score < 0.30:
            margin = (0.30 - score) / 0.30
            confidence = 0.60 + 0.15 * margin  # 0.60..0.75
            return ComplexityResponse(classification=ComplexityLevel.SIMPLE, confidence=round(confidence, 2))
        elif score < 0.60:
            # Distance from nearest boundary
            dist_low = score - 0.30
            dist_high = 0.60 - score
            margin = min(dist_low, dist_high) / 0.15
            confidence = 0.60 + 0.15 * margin
            return ComplexityResponse(classification=ComplexityLevel.MODERATE, confidence=round(confidence, 2))
        else:
            margin = min(1.0, (score - 0.60) / 0.40)
            confidence = 0.60 + 0.15 * margin
            return ComplexityResponse(classification=ComplexityLevel.COMPLEX, confidence=round(confidence, 2))

    # ----- Helpers -----

    @staticmethod
    def _matches_any(text: str, patterns: list[re.Pattern]) -> bool:
        return any(p.search(text) for p in patterns)
