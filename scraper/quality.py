"""
Text quality scoring for FPPC document extraction.

Scores extracted text on a 0.0-1.0 scale focused on *readability* — can a
human (or downstream search/RAG system) actually use this text?

Five components, all positive, weights sum to 1.0:

  density      (0.15) — Is content present? Words per page.
  char_quality (0.15) — Are characters clean? Alpha ratio.
  word_quality (0.15) — Are words structurally valid? (vowels, length, etc.)
  dict_score   (0.40) — Are words real English? Dictionary sampling. Dominant.
  content      (0.15) — Expected FPPC patterns (dates, headers, mentions).

The dictionary score is the strongest readability signal — it catches
character-level OCR corruptions (Califomia, Cornrnission, poritical) that
pass structural checks but aren't actual words.

Usage:
    from scraper.quality import compute_quality_score, should_use_olmocr

    metrics = compute_quality_score(text, page_count=5)
    print(f"Quality score: {metrics.final_score}")

    if should_use_olmocr(year=1985, metrics=metrics):
        ...
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


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
    alpha_ratio: float  # Raw ratio of alphabetic chars to printable chars

    # Component scores (each 0.0-1.0)
    density_score: float  # Content completeness (words per page)
    char_quality_score: float  # Character-level cleanliness
    word_quality_score: float  # Structural word validity
    dict_score: float  # Dictionary-based word validity
    content_score: float  # Expected document patterns

    # Final weighted score (0.0-1.0, uses full range)
    final_score: float

    # Diagnostic flags
    has_date_pattern: bool
    has_fppc_mention: bool
    has_section_headers: bool
    garbage_word_count: int  # Words that fail structural checks
    non_latin_word_count: int  # Words with CJK/Cyrillic/etc. characters
    dict_miss_ratio: float  # Fraction of sampled words not in dictionary


# =============================================================================
# Scoring Component Weights (sum to 1.0)
# =============================================================================

WEIGHT_DENSITY = 0.15  # Is content present?
WEIGHT_CHAR_QUALITY = 0.15  # Are characters clean?
WEIGHT_WORD_QUALITY = 0.15  # Are words structurally valid?
WEIGHT_DICT = 0.40  # Are words real English? (dominant signal)
WEIGHT_CONTENT = 0.15  # Does it look like an FPPC document?


# =============================================================================
# Dictionary Loading
# =============================================================================

_DICT_PATH = Path(__file__).parent / "data" / "common_english.txt"


@lru_cache(maxsize=1)
def _load_dictionary() -> frozenset[str]:
    """
    Lazily load and cache the English dictionary.

    Uses the bundled word list at scraper/data/common_english.txt.
    Falls back to system dictionary if bundled list is missing.
    """
    paths = [
        _DICT_PATH,
        Path("/usr/share/dict/american-english"),
        Path("/usr/share/dict/words"),
    ]

    for path in paths:
        if path.exists():
            words = set()
            with open(path) as f:
                for line in f:
                    w = line.strip()
                    if w and "'" not in w:  # Skip possessives
                        words.add(w.lower())
            return frozenset(words)

    # Absolute fallback: empty set (dict scoring disabled)
    return frozenset()


# =============================================================================
# Utility
# =============================================================================


def _piecewise_linear(x: float, points: list[tuple[float, float]]) -> float:
    """
    Piecewise linear interpolation through a series of (x, y) points.

    Returns y0 for x <= x0, y_last for x >= x_last, and linearly
    interpolates between adjacent points otherwise.
    """
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return points[-1][1]


# Range for "normal" Latin/ASCII text — anything outside this in an English
# legal document is almost certainly OCR garbage.
_RE_NON_LATIN = re.compile(
    r'[\u0400-\u04FF'   # Cyrillic
    r'\u3000-\u9FFF'    # CJK unified ideographs + symbols
    r'\uF900-\uFAFF'    # CJK compatibility ideographs
    r'\uAC00-\uD7AF'    # Hangul
    r'\u0600-\u06FF'    # Arabic
    r'\u0900-\u097F'    # Devanagari
    r'\uFF00-\uFFEF'    # Fullwidth forms (ド, ゛, ヽ, etc.)
    r'\u3040-\u309F'    # Hiragana
    r'\u30A0-\u30FF'    # Katakana
    r']'
)


# =============================================================================
# Scoring Functions
# =============================================================================


def _compute_density_score(words_per_page: float) -> float:
    """
    Score based on words-per-page density (content completeness).

    FPPC legal documents typically have 200-500 words per page.
    Very low counts suggest failed extraction or image-only pages.
    Very high counts might indicate formatting issues or merged text.

    Uses smooth piecewise linear interpolation instead of step functions.
    """
    return _piecewise_linear(words_per_page, [
        (0, 0.0),      # No text at all
        (20, 0.05),     # Almost certainly failed extraction
        (50, 0.30),     # Sparse — partial extraction
        (100, 0.60),    # Below expected but something is there
        (200, 0.95),    # Entering the sweet spot
        (600, 1.0),     # Full sweet spot for legal docs
        (800, 0.85),    # Slightly dense but acceptable
        (1200, 0.60),   # Suspiciously dense
    ])


def _compute_char_quality_score(text: str) -> tuple[float, float]:
    """
    Score based on ratio of alphabetic characters to all printable chars.

    Clean text extraction produces mostly letters. OCR garbage introduces
    symbols, stray digits, and control characters that lower this ratio.
    Legal documents typically run 85-95% alphabetic (excluding whitespace).

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

    score = _piecewise_linear(alpha_ratio, [
        (0.30, 0.0),    # Mostly non-alpha — binary or heavy garbage
        (0.50, 0.15),   # Significant noise
        (0.65, 0.40),   # Degraded but partially readable
        (0.75, 0.65),   # Noticeable issues but usable
        (0.85, 0.90),   # Good — minor noise
        (0.93, 1.0),    # Excellent — clean text
    ])

    return score, alpha_ratio


# Pre-compiled regexes for word quality checks (avoid recompiling per word)
_RE_REPEATED_CHARS = re.compile(r'(.)\1{3,}')  # 4+ repeated chars
_RE_CONSONANT_CLUSTER = re.compile(r'[bcdfghjklmnpqrstvwxz]{5,}')
_RE_HAS_VOWEL = re.compile(r'[aeiouyAEIOUY]')


def _compute_word_quality_score(text: str) -> tuple[float, int, int]:
    """
    Score based on what fraction of words are structurally valid.

    Catches OCR failures that produce character soup: long garbage strings,
    vowel-less words, repeated characters, consonant clusters, and
    non-Latin script injected into English text.

    Returns:
        Tuple of (score, garbage_word_count, non_latin_word_count)
    """
    words = text.split()
    if not words:
        return 0.0, 0, 0

    garbage_count = 0
    non_latin_count = 0

    for word in words:
        # Strip punctuation for analysis
        clean = word.strip('.,;:!?()[]{}"\'-/')
        if not clean:
            continue  # Pure punctuation is fine
        if len(clean) <= 2:
            continue  # Short tokens are fine (a, I, of, etc.)

        is_garbage = False

        # Non-Latin characters (CJK, Cyrillic, Arabic, etc.)
        # In an English legal document, these are definitively OCR garbage.
        if _RE_NON_LATIN.search(clean):
            is_garbage = True
            non_latin_count += 1

        # Too long (and not a URL/email/path)
        if not is_garbage and len(clean) > 25:
            if not clean.startswith(('http://', 'https://', 'www.')):
                if '@' not in clean:
                    is_garbage = True

        # No vowel in a word of 3+ characters
        if not is_garbage and len(clean) >= 3:
            if not _RE_HAS_VOWEL.search(clean):
                # Allow all-digit tokens (section numbers, dates)
                if not clean.replace('-', '').replace('.', '').isdigit():
                    is_garbage = True

        # Repeated characters (llllll, xxxxxxx)
        if not is_garbage and _RE_REPEATED_CHARS.search(clean):
            is_garbage = True

        # Excessive consonant clusters
        if not is_garbage and _RE_CONSONANT_CLUSTER.search(clean.lower()):
            is_garbage = True

        if is_garbage:
            garbage_count += 1

    # Score is the fraction of words that are NOT garbage
    valid_ratio = 1.0 - (garbage_count / len(words))

    score = _piecewise_linear(valid_ratio, [
        (0.50, 0.0),    # Half the words are garbage — unreadable
        (0.70, 0.20),   # Heavily degraded
        (0.85, 0.50),   # Noticeable issues
        (0.93, 0.75),   # Minor issues, mostly readable
        (0.97, 0.90),   # A few stray garbage words
        (1.0, 1.0),     # Clean
    ])

    return score, garbage_count, non_latin_count


def _compute_dict_score(text: str) -> tuple[float, float]:
    """
    Score based on what fraction of words are real English words.

    This is the strongest readability signal. It catches character-level OCR
    corruptions that produce "valid-looking" but wrong words — something
    structural checks miss entirely:

        Califomia  (rn→m)    — has vowels, reasonable length, passes structural
        Cornrnission (rn→m)  — same
        poritical  (l→r)    — same
        te).ephone           — would fail structural, but many similar don't

    Samples up to 200 words from the text (evenly spaced for coverage),
    strips punctuation, and checks each against a ~73K-word English dictionary.

    Returns:
        Tuple of (score, miss_ratio)
    """
    dictionary = _load_dictionary()
    if not dictionary:
        return 1.0, 0.0  # No dictionary available — skip this check

    words = text.split()
    if len(words) < 10:
        return 0.5, 0.5  # Too few words to sample meaningfully

    # Sample evenly across the document for coverage (not just the start)
    max_sample = 200
    if len(words) <= max_sample:
        sample = words
    else:
        step = len(words) / max_sample
        sample = [words[int(i * step)] for i in range(max_sample)]

    checked = 0
    misses = 0

    for word in sample:
        # Strip punctuation and normalize
        clean = word.strip('.,;:!?()[]{}"\'-/').lower()

        if not clean:
            continue

        # Skip pure numbers (section refs, dates, zip codes)
        if clean.replace('-', '').replace('.', '').replace(',', '').isdigit():
            continue

        # Skip very short words (too ambiguous)
        if len(clean) <= 2:
            continue

        # Skip words with embedded special chars (URLs, file refs)
        if any(c in clean for c in '@#$%&*=+<>'):
            continue

        checked += 1

        if clean not in dictionary:
            misses += 1

    if checked == 0:
        return 0.5, 0.5

    miss_ratio = misses / checked

    # Legal documents legitimately have ~10-15% words not in a standard
    # dictionary (proper nouns, legal terms, section references). So we
    # calibrate: 15% miss rate = good, 30% = degraded, 50%+ = garbage.
    score = _piecewise_linear(1.0 - miss_ratio, [
        (0.40, 0.0),    # 60%+ miss rate — mostly not English
        (0.55, 0.15),   # 45% miss rate — severely degraded
        (0.65, 0.35),   # 35% miss rate — heavily degraded
        (0.75, 0.60),   # 25% miss rate — noticeable issues
        (0.85, 0.80),   # 15% miss rate — minor issues, expected for legal
        (0.92, 0.95),   # 8% miss rate — very clean
        (1.0, 1.0),     # Perfect — every word in dictionary
    ])

    return score, miss_ratio


def _compute_content_score(text: str) -> tuple[float, bool, bool, bool]:
    """
    Score based on presence of expected FPPC document patterns.

    This is a document *validity* signal, not a direct readability measure.
    It confirms the extraction produced text that looks like an actual FPPC
    advice letter rather than random noise that happens to be readable.

    Checks for:
    - Date patterns (month day, year or MM/DD/YYYY)
    - References to FPPC / Fair Political Practices Commission
    - Section headers (QUESTION, CONCLUSION, FACTS, ANALYSIS)

    Returns:
        Tuple of (score, has_date, has_fppc, has_sections)
    """
    text_upper = text.upper()

    # Date patterns
    date_patterns = [
        r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b',
        r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',
    ]
    has_date = any(re.search(p, text, re.IGNORECASE) for p in date_patterns)

    # FPPC mentions
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
    has_sections = section_count >= 2

    # Compute score (each signal contributes independently)
    score = 0.0
    if has_date:
        score += 0.33
    if has_fppc:
        score += 0.34
    if has_sections:
        score += 0.33

    return score, has_date, has_fppc, has_sections


# =============================================================================
# Main Scoring Function
# =============================================================================


def compute_quality_score(text: str, page_count: int) -> QualityMetrics:
    """
    Compute comprehensive quality metrics for extracted text.

    Produces a score on the full 0.0-1.0 range focused on readability:
    how well can a human (or search system) actually use this text?

    Args:
        text: The extracted text content
        page_count: Number of pages in the source PDF

    Returns:
        QualityMetrics with detailed scoring breakdown
    """
    if not text or page_count <= 0:
        return QualityMetrics(
            total_chars=0,
            total_words=0,
            page_count=page_count,
            words_per_page=0.0,
            alpha_ratio=0.0,
            density_score=0.0,
            char_quality_score=0.0,
            word_quality_score=0.0,
            dict_score=0.0,
            content_score=0.0,
            final_score=0.0,
            has_date_pattern=False,
            has_fppc_mention=False,
            has_section_headers=False,
            garbage_word_count=0,
            non_latin_word_count=0,
            dict_miss_ratio=1.0,
        )

    # Basic measurements
    total_chars = len(text)
    words = text.split()
    total_words = len(words)
    words_per_page = total_words / page_count if page_count > 0 else 0

    # Compute component scores
    density = _compute_density_score(words_per_page)
    char_quality, alpha_ratio = _compute_char_quality_score(text)
    word_quality, garbage_count, non_latin_count = _compute_word_quality_score(text)
    dict_quality, dict_miss_ratio = _compute_dict_score(text)
    content, has_date, has_fppc, has_sections = _compute_content_score(text)

    # Weighted sum — all positive, weights sum to 1.0
    final_score = (
        WEIGHT_DENSITY * density
        + WEIGHT_CHAR_QUALITY * char_quality
        + WEIGHT_WORD_QUALITY * word_quality
        + WEIGHT_DICT * dict_quality
        + WEIGHT_CONTENT * content
    )

    # Density gate: if there's almost no text, the document isn't usable
    # regardless of how clean the few words happen to be.
    if density < 0.20:
        final_score *= density / 0.20

    # Clamp to [0.0, 1.0]
    final_score = min(1.0, max(0.0, final_score))

    return QualityMetrics(
        total_chars=total_chars,
        total_words=total_words,
        page_count=page_count,
        words_per_page=words_per_page,
        alpha_ratio=alpha_ratio,
        density_score=density,
        char_quality_score=char_quality,
        word_quality_score=word_quality,
        dict_score=dict_quality,
        content_score=content,
        final_score=final_score,
        has_date_pattern=has_date,
        has_fppc_mention=has_fppc,
        has_section_headers=has_sections,
        garbage_word_count=garbage_count,
        non_latin_word_count=non_latin_count,
        dict_miss_ratio=dict_miss_ratio,
    )


# =============================================================================
# OCR Decision Logic
# =============================================================================

# Thresholds for OCR fallback
OCR_YEAR_THRESHOLD = 1990  # Documents before this year likely need OCR
OCR_QUALITY_THRESHOLD = 0.5  # Below this score, use OCR
OCR_WORDS_PER_PAGE_MIN = 80  # Below this, extraction likely failed
OCR_ALPHA_RATIO_MIN = 0.70  # Below this, too much garbage (raw ratio)


def should_use_olmocr(year: int, metrics: QualityMetrics) -> bool:
    """
    Determine whether to use OCR for a document.

    Decision is conservative — we prefer false positives (unnecessary OCR)
    over false negatives (missed bad extraction), since OCR can always
    produce better or equal results for scanned documents.

    Args:
        year: Document year (from metadata)
        metrics: Quality metrics from compute_quality_score()

    Returns:
        True if OCR should be used, False otherwise
    """
    # Era-based trigger: pre-1990 documents are often scanned
    if year < OCR_YEAR_THRESHOLD:
        return True

    # Quality-based triggers
    if metrics.final_score < OCR_QUALITY_THRESHOLD:
        return True

    if metrics.words_per_page < OCR_WORDS_PER_PAGE_MIN:
        return True

    if metrics.alpha_ratio < OCR_ALPHA_RATIO_MIN:
        return True

    # High garbage word count is a strong signal of OCR issues
    if metrics.garbage_word_count > 5:
        return True

    return False
