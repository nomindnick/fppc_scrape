"""
Heuristic topic classifier for FPPC advice letters.

This module classifies documents based on the Government Code sections they
cite. The Political Reform Act is organized into distinct topic areas:

- **Conflicts of Interest** (§§ 87100-87500): Rules governing when public
  officials must disqualify themselves from decisions affecting their
  financial interests. Includes economic disclosure (SEI) requirements.

- **Campaign Finance** (§§ 84100-85800, 89500-89600): Campaign reporting
  requirements, contribution limits, expenditure rules, and mass mailing
  restrictions.

- **Lobbying** (§§ 86100-86400): Lobbyist registration and reporting
  requirements.

Usage:
    from scraper.classifier import classify_by_citations

    citations = ["87100", "87103(a)", "87200"]
    result = classify_by_citations(citations)
    print(result.topic_primary)  # "conflicts_of_interest"
    print(result.confidence)     # 1.0
"""

import re
from dataclasses import dataclass
from typing import Literal


# =============================================================================
# Type Definitions
# =============================================================================


TopicType = Literal["conflicts_of_interest", "campaign_finance", "lobbying", "other"]


# =============================================================================
# Result Dataclass
# =============================================================================


@dataclass
class ClassificationResult:
    """
    Result of topic classification.

    Attributes:
        topic_primary: The primary topic, or None if no citations provided.
        confidence: Confidence score from 0.0 to 1.0 (proportion of citations
            matching the primary topic).
        method: Description of the classification method used.
        section_counts: Count of citations per topic (for debugging).
    """

    topic_primary: TopicType | None
    confidence: float
    method: str
    section_counts: dict[str, int]


# =============================================================================
# Topic Range Definitions
# =============================================================================


# Political Reform Act section ranges organized by topic area.
# Based on the structure of the California Government Code.
#
# Note: Some ranges overlap conceptually (e.g., 87200-87220 covers disclosure
# within conflicts of interest). Ranges are checked in order and first match
# wins, but since we count per topic, the order doesn't matter for our use case.

TOPIC_RANGES: dict[str, list[range]] = {
    "conflicts_of_interest": [
        range(87100, 87501),  # General conflicts, disqualification (87100-87500)
        range(87200, 87221),  # Economic disclosure - SEI (87200-87220)
        range(87300, 87316),  # Designated employees, disclosure (87300-87315)
    ],
    "campaign_finance": [
        range(84100, 84601),  # Campaign reporting requirements (84100-84600)
        range(85100, 85801),  # Contributions and expenditures (85100-85800)
        range(89500, 89601),  # Mass mailing restrictions (89500-89600)
    ],
    "lobbying": [
        range(86100, 86401),  # Lobbyist registration and reporting (86100-86400)
    ],
}


# =============================================================================
# Helper Functions
# =============================================================================


def _extract_base_section(citation: str) -> int | None:
    """
    Extract the base section number from a citation string.

    Government Code citations may include subsections like "(a)", "(1)",
    or "(a)(1)". This function extracts just the base 5-digit section number.

    Args:
        citation: A Government Code citation, e.g., "87103(a)" or "87100"

    Returns:
        The base section number as an integer, or None if parsing fails.

    Examples:
        >>> _extract_base_section("87103(a)")
        87103
        >>> _extract_base_section("87100")
        87100
        >>> _extract_base_section("87200(a)(1)")
        87200
    """
    match = re.match(r"(\d+)", citation.strip())
    if match:
        return int(match.group(1))
    return None


def _classify_section(section_num: int) -> TopicType:
    """
    Classify a single section number into a topic.

    Args:
        section_num: The Government Code section number (e.g., 87100)

    Returns:
        The topic this section belongs to, or "other" if unrecognized.
    """
    for topic, ranges in TOPIC_RANGES.items():
        for r in ranges:
            if section_num in r:
                return topic  # type: ignore[return-value]
    return "other"


# =============================================================================
# Main Classification Function
# =============================================================================


def classify_by_citations(government_code_citations: list[str]) -> ClassificationResult:
    """
    Classify a document's topic based on its Government Code citations.

    The classifier counts how many citations fall into each topic area and
    returns the topic with the highest count. Confidence is calculated as
    the proportion of citations matching the primary topic.

    Args:
        government_code_citations: List of Government Code section citations
            as extracted by citation_extractor.py. May include subsections
            like "87103(a)".

    Returns:
        ClassificationResult with:
        - topic_primary: The most-cited topic, or None if no valid citations
        - confidence: Proportion of citations matching primary topic (0.0-1.0)
        - method: "heuristic:citation_based"
        - section_counts: Dictionary of {topic: count} for debugging

    Examples:
        >>> result = classify_by_citations(["87100", "87103(a)", "87200"])
        >>> result.topic_primary
        'conflicts_of_interest'
        >>> result.confidence
        1.0

        >>> result = classify_by_citations(["87100", "84200"])
        >>> result.topic_primary
        'conflicts_of_interest'
        >>> result.confidence
        0.5

        >>> result = classify_by_citations([])
        >>> result.topic_primary is None
        True
    """
    # Handle empty input
    if not government_code_citations:
        return ClassificationResult(
            topic_primary=None,
            confidence=0.0,
            method="heuristic:citation_based",
            section_counts={},
        )

    # Count citations per topic
    topic_counts: dict[str, int] = {
        "conflicts_of_interest": 0,
        "campaign_finance": 0,
        "lobbying": 0,
        "other": 0,
    }

    valid_citation_count = 0

    for citation in government_code_citations:
        base_section = _extract_base_section(citation)
        if base_section is not None:
            topic = _classify_section(base_section)
            topic_counts[topic] += 1
            valid_citation_count += 1

    # Handle case where all citations failed to parse
    if valid_citation_count == 0:
        return ClassificationResult(
            topic_primary=None,
            confidence=0.0,
            method="heuristic:citation_based",
            section_counts=topic_counts,
        )

    # Find the topic with highest count
    # Use sorted to ensure deterministic results on ties (alphabetical order wins)
    primary_topic = max(
        sorted(topic_counts.keys()),
        key=lambda t: topic_counts[t],
    )
    primary_count = topic_counts[primary_topic]

    # Calculate confidence as proportion of citations matching primary topic
    confidence = primary_count / valid_citation_count

    return ClassificationResult(
        topic_primary=primary_topic if primary_count > 0 else None,  # type: ignore[arg-type]
        confidence=confidence,
        method="heuristic:citation_based",
        section_counts=topic_counts,
    )
