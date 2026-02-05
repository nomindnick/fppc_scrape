"""
Text quality scoring for FPPC document extraction.

This module evaluates extracted PDF text quality and determines whether
olmOCR fallback is needed. It serves two critical functions:

1. Quality Assessment: Score extracted text (0.0-1.0) based on heuristics
2. OCR Decision: Determine whether to use olmOCR for poor native extraction

Usage:
    from scraper.quality import compute_quality_score, should_use_olmocr

    # Compute quality metrics for extracted text
    metrics = compute_quality_score(text, page_count=5)
    print(f"Quality score: {metrics.final_score}")

    # Decide whether to use olmOCR
    if should_use_olmocr(year=1985, metrics=metrics):
        # Fall back to olmOCR processing
        ...
"""

import re
from dataclasses import dataclass


# =============================================================================
# Quality Metrics Dataclass
# =============================================================================


@dataclass
class QualityMetrics:
    """
    Detailed breakdown of text quality scoring.

    This is internal debugging info, not part of the final JSON output schema.
    The final_score is what gets stored in ExtractionInfo.quality_score.
    """

    # Raw measurements
    total_chars: int
    total_words: int
    page_count: int
    words_per_page: float

    # Component scores (0.0-1.0)
    words_per_page_score: float  # Based on expected 200-500 words/page
    alpha_ratio_score: float  # Percentage of alphabetic characters
    pattern_score: float  # Presence of expected legal document patterns
    artifact_penalty: float  # Penalty for OCR garbage (0.0 = no penalty)

    # Final weighted score
    final_score: float

    # Diagnostic flags
    has_date_pattern: bool
    has_fppc_mention: bool
    has_section_headers: bool
    long_garbage_words: int  # Count of suspiciously long non-dictionary words


# =============================================================================
# Scoring Component Weights
# =============================================================================

# These weights sum to 1.0
WEIGHT_WORDS_PER_PAGE = 0.30
WEIGHT_ALPHA_RATIO = 0.25
WEIGHT_PATTERNS = 0.25
WEIGHT_ARTIFACTS = 0.20  # This is a penalty, subtracted from score


# =============================================================================
# Scoring Functions
# =============================================================================


def _compute_words_per_page_score(words_per_page: float) -> float:
    """
    Score based on words per page density.

    FPPC legal documents typically have 200-500 words per page.
    Very low counts suggest extraction failure or image-only pages.
    Very high counts might indicate merged text or formatting issues.

    Returns:
        Score from 0.0 to 1.0
    """
    if words_per_page < 20:
        # Almost certainly failed extraction
        return 0.0
    elif words_per_page < 80:
        # Sparse - likely partial extraction
        return 0.3
    elif words_per_page < 150:
        # Below expected range
        return 0.6
    elif words_per_page <= 600:
        # Good range for legal documents
        return 1.0
    elif words_per_page <= 800:
        # Slightly dense but acceptable
        return 0.8
    else:
        # Suspiciously dense - possible formatting issues
        return 0.5


def _compute_alpha_ratio_score(text: str) -> tuple[float, float]:
    """
    Score based on ratio of alphabetic characters.

    Good text extraction produces mostly letters and spaces.
    OCR garbage often has high ratios of symbols and numbers.

    Returns:
        Tuple of (score, raw_alpha_ratio)
    """
    if not text:
        return 0.0, 0.0

    alpha_count = sum(1 for c in text if c.isalpha())
    total_printable = sum(1 for c in text if c.isprintable() and not c.isspace())

    if total_printable == 0:
        return 0.0, 0.0

    alpha_ratio = alpha_count / total_printable

    # Legal documents should be ~85-95% alphabetic (excluding spaces)
    if alpha_ratio >= 0.85:
        return 1.0, alpha_ratio
    elif alpha_ratio >= 0.75:
        return 0.8, alpha_ratio
    elif alpha_ratio >= 0.60:
        return 0.5, alpha_ratio
    elif alpha_ratio >= 0.40:
        return 0.3, alpha_ratio
    else:
        return 0.0, alpha_ratio


def _compute_pattern_score(text: str) -> tuple[float, bool, bool, bool]:
    """
    Score based on presence of expected legal document patterns.

    FPPC advice letters typically contain:
    - Date patterns (month day, year)
    - References to FPPC or Fair Political Practices Commission
    - Section headers (QUESTION, CONCLUSION, FACTS, ANALYSIS)

    Returns:
        Tuple of (score, has_date, has_fppc, has_sections)
    """
    text_upper = text.upper()

    # Date pattern: "January 15, 2024" or "01/15/2024"
    date_patterns = [
        r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b',
        r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',
    ]
    has_date = any(re.search(p, text, re.IGNORECASE) for p in date_patterns)

    # FPPC mention
    fppc_patterns = [
        r'\bFPPC\b',
        r'FAIR\s+POLITICAL\s+PRACTICES\s+COMMISSION',
        r'POLITICAL\s+REFORM\s+ACT',
    ]
    has_fppc = any(re.search(p, text_upper) for p in fppc_patterns)

    # Section headers
    section_patterns = [
        r'\bQUESTION\b',
        r'\bCONCLUSION\b',
        r'\bFACTS\b',
        r'\bANALYSIS\b',
        r'\bSHORT\s+ANSWER\b',
    ]
    section_count = sum(1 for p in section_patterns if re.search(p, text_upper))
    has_sections = section_count >= 2  # At least 2 standard sections

    # Compute score
    score = 0.0
    if has_date:
        score += 0.33
    if has_fppc:
        score += 0.34
    if has_sections:
        score += 0.33

    return score, has_date, has_fppc, has_sections


def _compute_artifact_penalty(text: str) -> tuple[float, int]:
    """
    Compute penalty for OCR artifacts and garbage text.

    Common OCR failures produce:
    - Very long "words" that are actually multiple fused characters
    - Sequences of repeated characters
    - High concentration of unusual character combinations

    Returns:
        Tuple of (penalty_score, garbage_word_count)
    """
    if not text:
        return 0.0, 0

    # Find words that are suspiciously long (> 25 chars) and likely garbage
    words = text.split()
    long_words = [w for w in words if len(w) > 25]

    # Filter to only non-dictionary-like words (URLs excluded)
    garbage_words = []
    for word in long_words:
        # Skip URLs
        if word.startswith(('http://', 'https://', 'www.')):
            continue
        # Skip email addresses
        if '@' in word and '.' in word:
            continue
        # Check for excessive repeated characters or consonant clusters
        if re.search(r'(.)\1{4,}', word):  # 5+ repeated chars
            garbage_words.append(word)
        elif len(re.findall(r'[bcdfghjklmnpqrstvwxz]{6,}', word.lower())) > 0:
            # 6+ consonants in a row is suspicious
            garbage_words.append(word)

    garbage_count = len(garbage_words)

    # Penalty increases with garbage word count
    if garbage_count == 0:
        return 0.0, 0
    elif garbage_count <= 2:
        return 0.1, garbage_count
    elif garbage_count <= 5:
        return 0.3, garbage_count
    elif garbage_count <= 10:
        return 0.5, garbage_count
    else:
        return 0.8, garbage_count


def compute_quality_score(text: str, page_count: int) -> QualityMetrics:
    """
    Compute comprehensive quality metrics for extracted text.

    Args:
        text: The extracted text content
        page_count: Number of pages in the source PDF

    Returns:
        QualityMetrics with detailed scoring breakdown
    """
    # Handle edge cases
    if not text or page_count <= 0:
        return QualityMetrics(
            total_chars=0,
            total_words=0,
            page_count=page_count,
            words_per_page=0.0,
            words_per_page_score=0.0,
            alpha_ratio_score=0.0,
            pattern_score=0.0,
            artifact_penalty=0.0,
            final_score=0.0,
            has_date_pattern=False,
            has_fppc_mention=False,
            has_section_headers=False,
            long_garbage_words=0,
        )

    # Basic measurements
    total_chars = len(text)
    words = text.split()
    total_words = len(words)
    words_per_page = total_words / page_count if page_count > 0 else 0

    # Compute component scores
    wpp_score = _compute_words_per_page_score(words_per_page)
    alpha_score, _ = _compute_alpha_ratio_score(text)
    pattern_score, has_date, has_fppc, has_sections = _compute_pattern_score(text)
    artifact_penalty, garbage_count = _compute_artifact_penalty(text)

    # Weighted average with artifact penalty
    weighted_sum = (
        WEIGHT_WORDS_PER_PAGE * wpp_score
        + WEIGHT_ALPHA_RATIO * alpha_score
        + WEIGHT_PATTERNS * pattern_score
    )

    # Apply artifact penalty
    final_score = max(0.0, weighted_sum - (WEIGHT_ARTIFACTS * artifact_penalty))

    # Clamp to [0.0, 1.0]
    final_score = min(1.0, max(0.0, final_score))

    return QualityMetrics(
        total_chars=total_chars,
        total_words=total_words,
        page_count=page_count,
        words_per_page=words_per_page,
        words_per_page_score=wpp_score,
        alpha_ratio_score=alpha_score,
        pattern_score=pattern_score,
        artifact_penalty=artifact_penalty,
        final_score=final_score,
        has_date_pattern=has_date,
        has_fppc_mention=has_fppc,
        has_section_headers=has_sections,
        long_garbage_words=garbage_count,
    )


# =============================================================================
# olmOCR Decision Logic
# =============================================================================

# Thresholds for olmOCR fallback
OLMOCR_YEAR_THRESHOLD = 1990  # Documents before this year likely need OCR
OLMOCR_QUALITY_THRESHOLD = 0.5  # Below this score, use OCR
OLMOCR_WORDS_PER_PAGE_MIN = 80  # Below this, extraction likely failed
OLMOCR_ALPHA_RATIO_MIN = 0.6  # Below this, too much garbage


def should_use_olmocr(year: int, metrics: QualityMetrics) -> bool:
    """
    Determine whether to use olmOCR for a document.

    Decision is conservative - we prefer false positives (unnecessary OCR)
    over false negatives (missed bad extraction), since OCR can always
    produce better or equal results for scanned documents.

    Args:
        year: Document year (from metadata)
        metrics: Quality metrics from compute_quality_score()

    Returns:
        True if olmOCR should be used, False otherwise
    """
    # Era-based trigger: pre-1990 documents are often scanned
    if year < OLMOCR_YEAR_THRESHOLD:
        return True

    # Quality-based triggers
    if metrics.final_score < OLMOCR_QUALITY_THRESHOLD:
        return True

    if metrics.words_per_page < OLMOCR_WORDS_PER_PAGE_MIN:
        return True

    if metrics.alpha_ratio_score < OLMOCR_ALPHA_RATIO_MIN:
        return True

    # High garbage word count is a strong signal of OCR issues
    if metrics.long_garbage_words > 5:
        return True

    return False
