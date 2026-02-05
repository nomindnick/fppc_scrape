"""
Data models for FPPC document extraction.

This module defines all dataclasses needed for Phase 3 text extraction,
following the structured JSON schema for extracted documents.

Usage:
    from scraper.schema import FPPCDocument, Sections, Citations, to_json, from_json

    # Create a document
    doc = FPPCDocument(id="A-24-006", year=2024, ...)

    # Serialize to JSON
    json_str = to_json(doc)

    # Deserialize from JSON
    doc = from_json(json_str)
"""

from dataclasses import dataclass, asdict
from typing import Literal
import json


# =============================================================================
# Type Aliases for Literal Types
# =============================================================================

ExtractionMethod = Literal["native", "olmocr", "native+olmocr"]
DocumentType = Literal[
    "advice_letter", "opinion", "informal_advice", "correspondence", "other", "unknown"
]
TopicType = Literal["conflicts_of_interest", "campaign_finance", "lobbying", "gifts_honoraria", "other"]
SectionExtractionMethod = Literal["regex", "regex_validated", "llm", "none"]
QASource = Literal["extracted", "synthetic", "mixed"]


# =============================================================================
# Nested Dataclasses
# =============================================================================


@dataclass
class SourceMetadata:
    """Metadata from the original crawl."""

    title_raw: str  # Raw title from search results
    tags: list[str]  # Tags from "Filed under"
    scraped_at: str  # ISO timestamp when we found this document
    source_page_url: str | None  # Which search page we found it on


@dataclass
class ExtractionInfo:
    """Information about the text extraction process."""

    method: ExtractionMethod  # "native", "olmocr", or "native+olmocr"
    extracted_at: str  # ISO timestamp
    page_count: int
    word_count: int
    char_count: int
    quality_score: float  # 0.0-1.0, based on heuristics
    olmocr_cost: float | None  # Cost in USD if olmOCR was used
    native_word_count: int | None  # Word count from native (for comparison)


@dataclass
class Content:
    """The actual extracted text content."""

    full_text: str  # Plain text (always present)
    full_text_markdown: str | None  # Markdown from olmOCR (when used)


@dataclass
class ParsedMetadata:
    """Metadata parsed from the document content."""

    date: str | None  # ISO date: "2024-01-23"
    date_raw: str | None  # As written: "January 23, 2024"
    requestor_name: str | None
    requestor_title: str | None  # e.g., "City Attorney"
    requestor_city: str | None
    document_type: DocumentType


@dataclass
class Sections:
    """Structured sections from the document."""

    # Extracted content (None if not found via regex)
    question: str | None  # The QUESTION section
    conclusion: str | None  # The CONCLUSION/SHORT ANSWER
    facts: str | None  # The FACTS section
    analysis: str | None  # The ANALYSIS section

    # Synthetic content (LLM-generated if extraction failed)
    question_synthetic: str | None  # Generated question for docs without Q section
    conclusion_synthetic: str | None  # Generated conclusion for docs without C section

    # Extraction metadata
    extraction_method: SectionExtractionMethod  # "regex", "regex_validated", "llm", "none"
    extraction_confidence: float  # 0.0-1.0
    has_standard_format: bool  # True if Q/C sections were found via regex
    parsing_notes: str | None  # Any issues encountered


@dataclass
class Citations:
    """Legal citations found in the document."""

    government_code: list[str]  # ["87100", "87103(a)", "87200"]
    regulations: list[str]  # ["18700", "18702.1", "18730"]
    prior_opinions: list[str]  # ["A-23-001", "I-22-015"]
    cited_by: list[str]  # Opinions that cite THIS document (populated in post-processing)
    external: list[str]  # Court cases and other citations


@dataclass
class Classification:
    """Topic classification."""

    topic_primary: TopicType | None
    topic_secondary: str | None
    topic_tags: list[str]  # Granular tags
    confidence: float | None  # Classification confidence 0.0-1.0
    classified_at: str | None  # ISO timestamp
    classification_method: str | None  # "heuristic:citation_based", "llm:claude-haiku", etc.


@dataclass
class EmbeddingContent:
    """Pre-computed content optimized for embedding generation."""

    qa_text: str  # question + conclusion (extracted or synthetic)
    qa_source: QASource  # "extracted", "synthetic", or "mixed"
    first_500_words: str  # Fallback for docs with no structure
    summary: str | None  # LLM-generated summary (optional)


# =============================================================================
# Top-Level Document
# =============================================================================


@dataclass
class FPPCDocument:
    """Complete structured document combining all components."""

    # Identity
    id: str  # Letter ID: "A-24-006"
    year: int
    pdf_url: str
    pdf_sha256: str
    local_pdf_path: str  # Relative path: "raw_pdfs/2024/24006.pdf"

    # Nested structures
    source_metadata: SourceMetadata
    extraction: ExtractionInfo
    content: Content
    parsed: ParsedMetadata
    sections: Sections
    citations: Citations
    classification: Classification
    embedding: EmbeddingContent


# =============================================================================
# Serialization Helpers
# =============================================================================


def to_json(doc: FPPCDocument, indent: int = 2) -> str:
    """
    Serialize an FPPCDocument to JSON.

    Args:
        doc: The FPPCDocument to serialize
        indent: JSON indentation level (default 2)

    Returns:
        JSON string representation
    """
    return json.dumps(asdict(doc), indent=indent, ensure_ascii=False)


def from_json(json_str: str) -> FPPCDocument:
    """
    Deserialize JSON to an FPPCDocument.

    Args:
        json_str: JSON string to deserialize

    Returns:
        FPPCDocument instance

    Raises:
        json.JSONDecodeError: If JSON is invalid
        KeyError: If required fields are missing
        TypeError: If field types don't match
    """
    data = json.loads(json_str)

    # Reconstruct nested dataclasses
    return FPPCDocument(
        id=data["id"],
        year=data["year"],
        pdf_url=data["pdf_url"],
        pdf_sha256=data["pdf_sha256"],
        local_pdf_path=data["local_pdf_path"],
        source_metadata=SourceMetadata(**data["source_metadata"]),
        extraction=ExtractionInfo(**data["extraction"]),
        content=Content(**data["content"]),
        parsed=ParsedMetadata(**data["parsed"]),
        sections=Sections(**data["sections"]),
        citations=Citations(**data["citations"]),
        classification=Classification(**data["classification"]),
        embedding=EmbeddingContent(**data["embedding"]),
    )
