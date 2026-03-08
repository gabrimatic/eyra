import logging
import base64
from io import BytesIO
from typing import List, Dict, Any, Optional

import spacy
import numpy as np
from dataclasses import dataclass
from enum import Enum
from PIL import Image
from pydantic import BaseModel, Field
from spacy.tokens import Doc, Token

# Your local keyword list for domain complexity detection
from chat.words import complexity_indicators

# LOGGING SETUP
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Optional: If you have CLIP for advanced image complexity scoring:
try:
    import torch
    import clip  # Official CLIP package

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Loading official CLIP model...")
    model, preprocess = clip.load("ViT-B/32", device=device)
    USE_CLIP = True
    logger.info(f"CLIP model loaded successfully on {device}")
except ImportError as e:
    logger.warning(f"CLIP dependencies not available: {e}")
    model = None
    preprocess = None
    USE_CLIP = False
except Exception as e:
    logger.error(f"Error loading CLIP model: {e}")
    model = None
    preprocess = None
    USE_CLIP = False


###############################################################################
# SPACY SETUP
###############################################################################
try:
    # Use a larger model for better doc similarity if possible:
    nlp = spacy.load("en_core_web_trf")
    logger.info("Loaded spaCy model: en_core_web_trf.")
except OSError:
    logger.warning(
        "Falling back to en_core_web_md, doc similarity may be less accurate."
    )
    nlp = spacy.load("en_core_web_md")

###############################################################################
# CREATE REFERENCE DOCS FOR SEMANTIC COMPARISON
###############################################################################
SIMPLE_REF_DOC = nlp(
    "This is a basic, everyday text with simple vocabulary and no advanced topics."
)
COMPLEX_REF_DOC = nlp(
    "A highly specialized and conceptually challenging text involving quantum mechanics or abstract mathematics."
)


###############################################################################
# ENUMS & MODELS
###############################################################################
class ComplexityLevel(str, Enum):
    """
    Enumeration for complexity levels.
    """

    SIMPLE = "Simple"
    MODERATE = "Moderate"
    COMPLEX = "Complex"


class ComplexityResponse(BaseModel):
    """
    The final classification result from our local logic.
    """

    classification: ComplexityLevel = Field(..., description="Complexity level.")
    confidence: float = Field(..., ge=0, le=1, description="Confidence [0..1]")

    class Config:
        use_enum_values = True


@dataclass
class ComplexityMetrics:
    """
    Data structure holding the local heuristics for complexity.
    """

    text_length: int
    vocabulary_richness: float
    syntactic_complexity: float
    domain_keyword_density: float
    pos_diversity: float
    named_entity_density: float
    embedding_norm: float
    semantic_complexity: float  # 0..1 measure from doc similarity
    local_complexity_score: float  # The combined 0..1 score from our heuristics

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text_length": self.text_length,
            "vocabulary_richness": self.vocabulary_richness,
            "syntactic_complexity": self.syntactic_complexity,
            "domain_keyword_density": self.domain_keyword_density,
            "pos_diversity": self.pos_diversity,
            "named_entity_density": self.named_entity_density,
            "embedding_norm": self.embedding_norm,
            "semantic_complexity": self.semantic_complexity,
            "local_complexity_score": self.local_complexity_score,
        }


###############################################################################
# MAIN CLASS
###############################################################################
class ComplexityScorer:
    """
    COMPLEXITY SCORER (LOCAL-ONLY VERSION)

    This class removes any remote AI calls. We rely entirely on local spaCy-based
    heuristics and optional CLIP analysis for images. We combine them into a single
    local complexity score and produce a classification:

       1) Simple
       2) Moderate
       3) Complex

    The classification is determined by thresholds on our 'local_complexity_score'.
    This approach includes:

    - Ratio-based linguistic metrics (vocabulary richness, syntactic depth, domain keywords, etc.).
    - A "semantic complexity" measure that compares the doc to reference "simple" and "complex" texts.
    - Handling for short texts to prevent inflated scores (dampening).
    - Optional local CLIP scoring for images (if you provide a base64 image).

    NOTE: If you want to refine the classification further, you can add a local ML
    model or expand the doc-similarity approach.
    """

    def __init__(self):
        logger.info("ComplexityScorer (local-only) initialized.")

    async def score_complexity(
        self,
        text_content: Optional[str],
        task_type: str,
        image_base64: Optional[str] = None,
    ) -> ComplexityResponse:
        """
        Main entry point for classifying complexity, entirely local.
        Now properly async to work with the rest of the async pipeline.
        """
        logger.info(f"[LOCAL] Scoring complexity for task type: {task_type}.")

        # Prepare default metrics
        metrics = ComplexityMetrics(
            text_length=0,
            vocabulary_richness=0.0,
            syntactic_complexity=0.0,
            domain_keyword_density=0.0,
            pos_diversity=0.0,
            named_entity_density=0.0,
            embedding_norm=0.0,
            semantic_complexity=0.0,
            local_complexity_score=0.0,
        )

        if text_content:
            # Build spaCy doc
            doc = nlp(text_content)
            # Compute local text metrics
            metrics = self._build_text_metrics(doc, text_content)

        # If image, optionally extract local complexity signals
        if task_type == "image" and image_base64:
            img_meta = self._extract_image_metadata(image_base64)
            if USE_CLIP and img_meta.get("valid"):
                clip_score = await self._clip_score(
                    img_meta["pil_image"], text_content or ""
                )
                logger.info(f"[LOCAL] CLIP-based complexity measure ~ {clip_score:.3f}")
                metrics.local_complexity_score = np.clip(
                    metrics.local_complexity_score + 0.1 * clip_score, 0.0, 1.0
                )

        # Convert the final local complexity score => classification
        classification_result = self._local_classify(metrics.local_complexity_score)
        logger.info(
            f"[LOCAL] Final classification based on local_score={metrics.local_complexity_score:.3f}"
            f" => {classification_result.classification}, conf={classification_result.confidence:.2f}"
        )
        return classification_result

    ############################################################################
    # INTERNAL LOGIC
    ############################################################################
    def _build_text_metrics(self, doc: Doc, text: str) -> ComplexityMetrics:
        """
        Construct ComplexityMetrics from spaCy-based heuristics + semantic checks.
        """
        tokens = [t for t in doc if not t.is_space]
        text_length = len(text.strip())

        # Basic metrics
        vocab_richness = self._calculate_vocab_richness(tokens)
        syntactic_complexity = self._calculate_syntactic_depth(doc)
        domain_keyword_density = self._calculate_domain_keywords(tokens)
        pos_diversity = self._calculate_pos_diversity(tokens)
        named_entity_density = self._calculate_named_entity_density(doc, tokens)
        embedding_norm = float(np.linalg.norm(doc.vector)) if doc.vector_norm else 0.0

        # Debug: raw doc similarity
        sim_to_simple = doc.similarity(SIMPLE_REF_DOC)
        sim_to_complex = doc.similarity(COMPLEX_REF_DOC)
        logger.info(
            f"[DEBUG] doc.similarity => simple={sim_to_simple:.3f}, complex={sim_to_complex:.3f}"
        )

        # Semantic measure (meaning-based)
        semantic_complexity = self._calculate_semantic_complexity(
            token_count=len(tokens),
            sim_simple=sim_to_simple,
            sim_complex=sim_to_complex,
        )

        # Combine everything => local complexity score
        local_score = self._compute_local_complexity_score(
            vocab_richness,
            syntactic_complexity,
            domain_keyword_density,
            pos_diversity,
            named_entity_density,
            embedding_norm,
            semantic_complexity,
            token_count=len(tokens),
        )

        logger.info(
            f"[DEBUG] Metrics => text_len={text_length}, vocab_r={vocab_richness:.3f}, syn={syntactic_complexity:.3f}, "
            f"domain={domain_keyword_density:.3f}, pos_div={pos_diversity:.3f}, NE={named_entity_density:.3f}, "
            f"embed_norm={embedding_norm:.3f}, sem_cmplx={semantic_complexity:.3f} => local_score={local_score:.3f}"
        )

        return ComplexityMetrics(
            text_length=text_length,
            vocabulary_richness=vocab_richness,
            syntactic_complexity=syntactic_complexity,
            domain_keyword_density=domain_keyword_density,
            pos_diversity=pos_diversity,
            named_entity_density=named_entity_density,
            embedding_norm=embedding_norm,
            semantic_complexity=semantic_complexity,
            local_complexity_score=local_score,
        )

    def _calculate_vocab_richness(self, tokens: List[Token]) -> float:
        """
        Ratio (#unique lemmas) / (#meaningful tokens),
        clipped to [0..1].
        """
        meaningful = [t for t in tokens if t.is_alpha and not t.is_stop]
        if not meaningful:
            return 0.0
        unique_lemmas = set(t.lemma_.lower() for t in meaningful)
        ratio = len(unique_lemmas) / float(len(meaningful))
        return min(1.0, ratio)

    def _calculate_syntactic_depth(self, doc: Doc) -> float:
        """
        Average dependency depth scaled to [0..1].
        """
        depths = []
        for sent in doc.sents:
            for token in sent:
                depths.append(self._get_dep_depth(token))
        if not depths:
            return 0.0
        avg_depth = sum(depths) / len(depths)
        return min(1.0, avg_depth / 10.0)  # 10 = "max depth"

    def _get_dep_depth(self, token: Token) -> int:
        depth = 0
        current = token
        while current.head != current:
            depth += 1
            current = current.head
        return depth

    def _calculate_domain_keywords(self, tokens: List[Token]) -> float:
        """
        # of domain keywords / total tokens, clipped to [0..1].
        """
        if not tokens:
            return 0.0
        lower_lemmas = [t.lemma_.lower() for t in tokens]
        hits = sum(
            1 for lem in lower_lemmas if any(kw in lem for kw in complexity_indicators)
        )
        ratio = hits / float(len(tokens))
        return min(1.0, ratio)

    def _calculate_pos_diversity(self, tokens: List[Token]) -> float:
        """
        Distinct POS tags / total tokens, clipped to [0..1].
        """
        if not tokens:
            return 0.0
        pos_tags = {t.pos_ for t in tokens}
        ratio = len(pos_tags) / len(tokens)
        return min(1.0, ratio)

    def _calculate_named_entity_density(self, doc: Doc, tokens: List[Token]) -> float:
        """
        # of NE tokens / total tokens, clipped to [0..1].
        """
        if not tokens:
            return 0.0
        total_ne_tokens = sum(len(ent) for ent in doc.ents)
        ratio = total_ne_tokens / float(len(tokens))
        return min(1.0, ratio)

    def _calculate_semantic_complexity(
        self, token_count: int, sim_simple: float, sim_complex: float
    ) -> float:
        """
        Return a 0..1 measure of 'meaning-based' complexity by comparing the doc
        to reference 'simple' and 'complex' texts. We do:

           raw_diff = sim_complex - sim_simple

        Then scale that difference to [0..1]. Also reduce if text is < 5 tokens.
        """
        # For short text, we reduce the effect to avoid random spikes
        short_factor = 0.2 if token_count < 5 else 1.0

        raw_diff = sim_complex - sim_simple  # can be negative or positive
        # Let's assume -0.3..+0.3 typical range => map to 0..1
        min_diff, max_diff = -0.3, 0.3
        normalized = (raw_diff - min_diff) / (
            max_diff - min_diff
        )  # in [0..1] if raw is in that range
        clipped = max(0.0, min(1.0, normalized))

        return clipped * short_factor

    def _compute_local_complexity_score(
        self,
        vocab_richness: float,
        syntactic_complexity: float,
        domain_keyword_density: float,
        pos_diversity: float,
        named_entity_density: float,
        embedding_norm: float,
        semantic_complexity: float,
        token_count: int,
    ) -> float:
        """
        Combine all local signals into one 0..1 complexity score.
        """
        # Check inputs in [0..1], except embedding_norm
        for x in [
            vocab_richness,
            syntactic_complexity,
            domain_keyword_density,
            pos_diversity,
            named_entity_density,
            semantic_complexity,
        ]:
            if not (0 <= x <= 1):
                raise ValueError(
                    "All inputs except embedding_norm must be between 0 and 1"
                )

        # embedding_norm can be up to ~300 => scale
        norm_embed = min(1.0, embedding_norm / 300.0)

        # If fewer than 5 tokens, we dampen the ratio-based scores to avoid over-inflation
        if token_count < 5:
            vocab_richness *= 0.5
            syntactic_complexity *= 0.5
            domain_keyword_density *= 0.5
            pos_diversity *= 0.5
            named_entity_density *= 0.5
            # semantic_complexity was already scaled by short_factor

        # Weight scheme
        weights = {
            "vocab_richness": 0.15,
            "syntactic_complexity": 0.15,
            "domain_keyword_density": 0.15,
            "pos_diversity": 0.10,
            "named_entity_density": 0.10,
            "embedding_norm": 0.05,
            "semantic_complexity": 0.30,
        }

        raw_score = (
            weights["vocab_richness"] * vocab_richness
            + weights["syntactic_complexity"] * syntactic_complexity
            + weights["domain_keyword_density"] * domain_keyword_density
            + weights["pos_diversity"] * pos_diversity
            + weights["named_entity_density"] * named_entity_density
            + weights["embedding_norm"] * norm_embed
            + weights["semantic_complexity"] * semantic_complexity
        )

        return max(0.0, min(1.0, raw_score))

    ############################################################################
    # LOCAL CLASSIFICATION
    ############################################################################
    def _local_classify(self, local_score: float) -> ComplexityResponse:
        """
        Map local_score in [0..1] to a final classification (SIMPLE, MODERATE, COMPLEX).
        We also produce a confidence measure (somewhat arbitrary).

        e.g.:
         - 0..0.33 => SIMPLE
         - 0.33..0.66 => MODERATE
         - 0.66..1.0 => COMPLEX

        Confidence can be the distance from the threshold boundary, for instance.
        """
        if local_score < 0.33:
            # Distance from boundary => confidence
            confidence = 0.5 + (0.33 - local_score) / 0.33 * 0.5
            return ComplexityResponse(
                classification=ComplexityLevel.SIMPLE, confidence=min(1.0, confidence)
            )
        elif local_score < 0.66:
            # Mid-range
            confidence = 0.5 + (0.66 - abs(0.5 - local_score)) / 0.16 * 0.3
            return ComplexityResponse(
                classification=ComplexityLevel.MODERATE, confidence=min(1.0, confidence)
            )
        else:
            # 0.66..1 => complex
            confidence = 0.5 + (local_score - 0.66) / 0.34 * 0.5
            return ComplexityResponse(
                classification=ComplexityLevel.COMPLEX, confidence=min(1.0, confidence)
            )

    ############################################################################
    # IMAGE HANDLING (OPTIONAL)
    ############################################################################
    def _extract_image_metadata(self, image_base64: str) -> Dict[str, Any]:
        """
        Decode base64-encoded image data and return metadata.
        """
        try:
            img_data = base64.b64decode(image_base64)
            pil_image = Image.open(BytesIO(img_data))
            pil_image.load()
            w, h = pil_image.size
            size_bytes = len(img_data)

            return {
                "valid": True,
                "dimensions": (w, h),
                "file_size_bytes": size_bytes,
                "mode": pil_image.mode,
                "format": pil_image.format,
                "pil_image": pil_image,
                "estimated_complexity": (
                    "high"
                    if size_bytes > 500000
                    else "medium" if size_bytes > 100000 else "low"
                ),
            }
        except Exception as e:
            logger.warning(f"Failed to extract image metadata: {e}")
            return {"valid": False}

    async def _clip_score(self, pil_image: Image.Image, text_excerpt: str) -> float:
        """
        Async version of CLIP scoring
        """
        if not (model and preprocess and pil_image):
            return 0.5

        try:
            import torch

            image_input = preprocess(pil_image).unsqueeze(0).to(device)
            text_input = clip.tokenize([text_excerpt or "a photograph"]).to(device)

            async with torch.no_grad():
                image_features = model.encode_image(image_input)
                text_features = model.encode_text(text_input)

                image_features /= image_features.norm(dim=-1, keepdim=True)
                text_features /= text_features.norm(dim=-1, keepdim=True)

                similarity = (100.0 * image_features @ text_features.T).softmax(dim=-1)
                feature_diversity = image_features.std(dim=-1).mean().item()
                feature_magnitude = image_features.norm(dim=-1).mean().item()

                raw_score = (
                    0.4 * feature_magnitude
                    + 0.4 * feature_diversity
                    + 0.2 * float(similarity[0, 0].cpu())
                )
                return torch.sigmoid(torch.tensor(raw_score)).item()

        except Exception as e:
            logger.error(f"Error in CLIP scoring: {str(e)}")
            return 0.5
