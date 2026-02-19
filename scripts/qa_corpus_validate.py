#!/usr/bin/env python3
"""
Comprehensive corpus validation for FPPC advice letter extraction.

Runs 9 quality checks across all 14,129 extracted JSON documents in a single pass,
producing a structured markdown report with flagged documents.

Checks:
  1. Citation existence — prior_opinions references point to real docs in corpus
  2. Date/year consistency — parsed.date year matches file's year directory
  3. Duplicate full_text — hash content.full_text to find identical documents
  4. Word/page outliers — flag extreme word_count/page_count ratios
  5. Section-in-full-text — extracted section text actually appears in full_text
  6. Zero-section categorization — group docs with no sections by type and era
  7. No-citation investigation — group docs with zero citations by type and era
  8. Boilerplate sweep — scan section fields and qa_text for known boilerplate
  9. Citation graph — build cited_by reverse index, flag dangling references

Usage:
    python scripts/qa_corpus_validate.py
    python scripts/qa_corpus_validate.py --json  # also write machine-readable output
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.section_parser import BOILERPLATE_PATTERNS


# =============================================================================
# Configuration
# =============================================================================

EXTRACTED_DIR = "data/extracted"
REPORT_PATH = "data/qa_reports/corpus_validation.md"
JSON_REPORT_PATH = "data/qa_reports/corpus_validation.json"

# Word/page ratio thresholds
MIN_WORDS_PER_PAGE = 50    # Below this suggests image-only pages
MAX_WORDS_PER_PAGE = 1500  # Above this suggests extraction artifact

# Section verification: minimum overlap ratio for substring match
# (handles minor whitespace normalization differences)
SECTION_MATCH_THRESHOLD = 0.8


# =============================================================================
# Data structures for collecting results
# =============================================================================

@dataclass
class ValidationResults:
    """Collects all validation findings across all checks."""

    total_docs: int = 0
    load_errors: list[str] = field(default_factory=list)

    # Check 1: Citation existence
    dangling_citations: dict[str, list[str]] = field(default_factory=dict)
    # doc_id -> list of cited opinion IDs not found in corpus
    valid_citation_count: int = 0
    total_prior_opinion_refs: int = 0

    # Check 2: Date/year consistency
    date_year_mismatches: list[dict] = field(default_factory=list)
    # [{doc_id, file_year, parsed_date, parsed_year}]
    no_date_count: int = 0

    # Check 3: Duplicate full_text
    duplicate_groups: dict[str, list[str]] = field(default_factory=dict)
    # hash -> [doc_id1, doc_id2, ...]

    # Check 4: Word/page outliers
    low_density: list[dict] = field(default_factory=list)
    # [{doc_id, word_count, page_count, ratio}]
    high_density: list[dict] = field(default_factory=list)

    # Check 5: Section-in-full-text
    section_not_in_text: list[dict] = field(default_factory=list)
    # [{doc_id, section, snippet}]
    sections_checked: int = 0

    # Check 6: Zero-section categorization
    zero_section_by_type: Counter = field(default_factory=Counter)
    zero_section_by_era: Counter = field(default_factory=Counter)
    zero_section_docs: list[str] = field(default_factory=list)

    # Check 7: No-citation investigation
    no_citation_by_type: Counter = field(default_factory=Counter)
    no_citation_by_era: Counter = field(default_factory=Counter)
    no_citation_docs: list[str] = field(default_factory=list)

    # Check 8: Boilerplate sweep
    boilerplate_hits: dict[str, list[dict]] = field(default_factory=dict)
    # pattern_name -> [{doc_id, field, snippet}]
    boilerplate_doc_count: int = 0
    boilerplate_field_counts: Counter = field(default_factory=Counter)

    # Check 9: Citation graph
    cited_by: dict[str, list[str]] = field(default_factory=dict)
    # target_id -> [citing_doc_id, ...]
    most_cited: list[tuple[str, int]] = field(default_factory=list)
    dangling_targets: dict[str, list[str]] = field(default_factory=dict)
    # non-existent target -> [citing_doc_ids]


# =============================================================================
# Utility functions
# =============================================================================

def era_bucket(year: int) -> str:
    """Assign a year to an era bucket for grouping."""
    if year <= 1983:
        return "1975-1983"
    elif year <= 1994:
        return "1984-1994"
    elif year <= 2005:
        return "1995-2005"
    elif year <= 2015:
        return "2006-2015"
    else:
        return "2016-2025"


def build_id_lookup(known_ids: set[str]) -> set[str]:
    """
    Build an expanded set of ID variants for citation matching.

    The corpus stores IDs in various formats across eras:
      - Modern: "A-22-078" (with prefix)
      - 1995-2015: "00-010", "99-228" (no prefix)
      - 1984-1994: "88367", "87031" (no prefix, no dashes)
      - Pre-1984: "82A060", "76604"

    But citation references normalize to "A-XX-NNN" format.
    This builds a lookup that maps both formats.
    """
    expanded = set(known_ids)

    for kid in known_ids:
        # Strip common prefixes to create bare form: "A-92-289" -> "92-289"
        m = re.match(r'^[AIM]-(\d{2}-\d{3,4})$', kid)
        if m:
            expanded.add(m.group(1))
            # Also add without dash: "92289"
            expanded.add(m.group(1).replace("-", ""))

        # Add prefixed forms for bare IDs: "92-289" -> "A-92-289", "I-92-289"
        m = re.match(r'^(\d{2})-(\d{3,4})$', kid)
        if m:
            for prefix in ["A", "I", "M"]:
                expanded.add(f"{prefix}-{kid}")

        # Handle old format "88367" -> "A-88-367"
        m = re.match(r'^(\d{2})(\d{3,4})$', kid)
        if m:
            for prefix in ["A", "I", "M"]:
                expanded.add(f"{prefix}-{m.group(1)}-{m.group(2)}")
            expanded.add(f"{m.group(1)}-{m.group(2)}")

        # Handle "82A060" -> "A-82-060"
        m = re.match(r'^(\d{2})A(\d{3,4})$', kid)
        if m:
            expanded.add(f"A-{m.group(1)}-{m.group(2)}")
            expanded.add(f"{m.group(1)}-{m.group(2)}")

    return expanded


def normalize_for_search(text: str) -> str:
    """Normalize text for substring matching (collapse whitespace)."""
    return re.sub(r'\s+', ' ', text.strip().lower())


def first_n_chars(text: str, n: int = 80) -> str:
    """Return first n characters of text for display."""
    if not text:
        return "(empty)"
    clean = text.replace('\n', ' ').strip()
    if len(clean) > n:
        return clean[:n] + "..."
    return clean


# Compile boilerplate patterns once
COMPILED_BOILERPLATE = []
for i, pat in enumerate(BOILERPLATE_PATTERNS):
    try:
        compiled = re.compile(pat, re.IGNORECASE | re.DOTALL)
        COMPILED_BOILERPLATE.append((f"pattern_{i}", compiled))
    except re.error:
        pass  # Skip patterns that fail to compile


# Human-readable names for boilerplate patterns
BOILERPLATE_NAMES = {
    "pattern_0": "PRA footnote (clean)",
    "pattern_1": "PRA footnote (OCR-tolerant)",
    "pattern_2": "PRA footnote (garbled)",
    "pattern_3": "Regulation footnote",
    "pattern_4": "Combined PRA+regulation footnote",
    "pattern_5": "Page header with file number",
    "pattern_6": "Re: line with file number",
    "pattern_7": "FPPC address block",
    "pattern_8": "Standalone page reference",
    "pattern_9": "Old-style footnote",
    "pattern_10": "Regulatory references sentence",
    "pattern_11": "Commission regulations footnote",
    "pattern_12": "Statutory references sentence",
    "pattern_13": "Informal assistance footnote (merged)",
    "pattern_14": "Informal assistance footnote (standalone)",
    "pattern_15": "FPPC letterhead (OCR-garbled)",
}


# =============================================================================
# Core validation logic (single pass)
# =============================================================================

def load_all_documents(extracted_dir: str) -> tuple[dict[str, dict], set[str], list[str]]:
    """
    Load all JSON documents and build the corpus ID set.

    Returns:
        (documents dict keyed by doc_id, set of all known letter_ids, load errors)
    """
    documents = {}
    known_ids = set()
    errors = []

    json_files = sorted(glob.glob(os.path.join(extracted_dir, "**", "*.json"), recursive=True))

    for path in json_files:
        try:
            with open(path) as f:
                doc = json.load(f)
            doc_id = doc.get("id") or os.path.basename(path).replace(".json", "")
            doc["_path"] = path
            doc["_file_year"] = int(os.path.basename(os.path.dirname(path)))
            documents[doc_id] = doc
            known_ids.add(doc_id)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            errors.append(f"{path}: {e}")

    return documents, known_ids, errors


def run_all_checks(documents: dict[str, dict], known_ids: set[str]) -> ValidationResults:
    """Run all 9 validation checks in a single pass over the corpus."""

    results = ValidationResults(total_docs=len(documents))

    # Build expanded ID lookup for citation matching (Check 1 & 9)
    expanded_ids = build_id_lookup(known_ids)

    # Pre-compute: content hashes for duplicate detection (Check 3)
    content_hashes = defaultdict(list)

    # Pre-compute: citation graph edges (Check 9)
    citation_edges = []  # (citing_doc_id, cited_doc_id)

    # Track boilerplate-contaminated docs
    boilerplate_contaminated_docs = set()

    for doc_id, doc in documents.items():
        year = doc.get("year", doc.get("_file_year", 0))
        file_year = doc.get("_file_year", 0)
        era = era_bucket(year) if year else "unknown"

        extraction = doc.get("extraction", {})
        content = doc.get("content", {})
        parsed = doc.get("parsed", {})
        sections = doc.get("sections", {})
        citations = doc.get("citations", {})
        embedding = doc.get("embedding", {})

        full_text = content.get("full_text", "")
        word_count = extraction.get("word_count", 0)
        page_count = extraction.get("page_count", 1) or 1  # avoid division by zero

        # ==== Check 1: Citation existence ====
        prior_opinions = citations.get("prior_opinions", [])
        for cited_id in prior_opinions:
            results.total_prior_opinion_refs += 1
            if cited_id in expanded_ids:
                results.valid_citation_count += 1
            else:
                if doc_id not in results.dangling_citations:
                    results.dangling_citations[doc_id] = []
                results.dangling_citations[doc_id].append(cited_id)

        # ==== Check 2: Date/year consistency ====
        parsed_date = parsed.get("date")
        if parsed_date:
            try:
                parsed_year = int(parsed_date[:4])
                # Allow 1-year tolerance (Dec filing → Jan response)
                if abs(parsed_year - file_year) > 1:
                    results.date_year_mismatches.append({
                        "doc_id": doc_id,
                        "file_year": file_year,
                        "parsed_date": parsed_date,
                        "parsed_year": parsed_year,
                        "delta": parsed_year - file_year,
                    })
            except (ValueError, IndexError):
                pass  # Malformed date, not a year mismatch per se
        else:
            results.no_date_count += 1

        # ==== Check 3: Duplicate full_text ====
        if full_text and len(full_text) > 100:
            text_hash = hashlib.sha256(full_text.encode()).hexdigest()[:16]
            content_hashes[text_hash].append(doc_id)

        # ==== Check 4: Word/page outliers ====
        ratio = word_count / page_count if page_count else 0
        if ratio < MIN_WORDS_PER_PAGE and word_count > 0:
            results.low_density.append({
                "doc_id": doc_id,
                "word_count": word_count,
                "page_count": page_count,
                "ratio": round(ratio, 1),
                "method": extraction.get("method", "unknown"),
            })
        elif ratio > MAX_WORDS_PER_PAGE:
            results.high_density.append({
                "doc_id": doc_id,
                "word_count": word_count,
                "page_count": page_count,
                "ratio": round(ratio, 1),
                "method": extraction.get("method", "unknown"),
            })

        # ==== Check 5: Section-in-full-text verification ====
        # Only verify regex-extracted sections (LLM sections are synthetic paraphrases)
        section_method = sections.get("extraction_method", "none")
        full_text_norm = normalize_for_search(full_text) if full_text else ""
        for section_name in ["question", "conclusion", "facts", "analysis"]:
            section_text = sections.get(section_name)
            if section_text and len(section_text) > 20 and section_method != "llm":
                results.sections_checked += 1
                # Use multiple short probes (5-word windows) from different positions
                # to handle gaps created by boilerplate/page-header removal during cleaning.
                # A section passes if ANY probe matches.
                words = section_text.split()
                probes_found = 0
                probes_total = 0
                failed_probe = ""

                if len(words) > 8:
                    # Sample 3 windows: 25%, 50%, 75% through the section
                    for frac in [0.25, 0.5, 0.75]:
                        pos = int(len(words) * frac)
                        probe = " ".join(words[max(0, pos-2):pos+3])
                        probe_norm = normalize_for_search(probe)
                        probes_total += 1
                        if probe_norm and probe_norm in full_text_norm:
                            probes_found += 1
                        elif not failed_probe:
                            failed_probe = probe
                else:
                    probe_norm = normalize_for_search(section_text)
                    probes_total = 1
                    if probe_norm and probe_norm in full_text_norm:
                        probes_found = 1
                    else:
                        failed_probe = section_text

                # Flag only if NONE of the probes matched
                if probes_found == 0 and probes_total > 0:
                    results.section_not_in_text.append({
                        "doc_id": doc_id,
                        "section": section_name,
                        "probe": first_n_chars(failed_probe, 100),
                        "section_method": sections.get("extraction_method", "unknown"),
                    })

        # ==== Check 6: Zero-section categorization ====
        has_sections = any(
            sections.get(s) for s in ["question", "conclusion", "facts", "analysis"]
        )
        if not has_sections:
            doc_type = parsed.get("document_type", "unknown")
            results.zero_section_by_type[doc_type] += 1
            results.zero_section_by_era[era] += 1
            results.zero_section_docs.append(doc_id)

        # ==== Check 7: No-citation investigation ====
        has_citations = (
            citations.get("government_code")
            or citations.get("regulations")
            or citations.get("prior_opinions")
            or citations.get("external")
        )
        if not has_citations:
            doc_type = parsed.get("document_type", "unknown")
            results.no_citation_by_type[doc_type] += 1
            results.no_citation_by_era[era] += 1
            results.no_citation_docs.append(doc_id)

        # ==== Check 8: Boilerplate sweep ====
        fields_to_check = {
            "question": sections.get("question", ""),
            "conclusion": sections.get("conclusion", ""),
            "facts": sections.get("facts", ""),
            "analysis": sections.get("analysis", ""),
            "qa_text": embedding.get("qa_text", ""),
        }

        doc_has_boilerplate = False
        for field_name, field_text in fields_to_check.items():
            if not field_text:
                continue
            for pattern_name, compiled_pat in COMPILED_BOILERPLATE:
                match = compiled_pat.search(field_text)
                if match:
                    doc_has_boilerplate = True
                    if pattern_name not in results.boilerplate_hits:
                        results.boilerplate_hits[pattern_name] = []
                    # Only store first 50 examples per pattern to keep report manageable
                    if len(results.boilerplate_hits[pattern_name]) < 50:
                        results.boilerplate_hits[pattern_name].append({
                            "doc_id": doc_id,
                            "field": field_name,
                            "snippet": first_n_chars(match.group(0), 100),
                        })
                    results.boilerplate_field_counts[field_name] += 1

        if doc_has_boilerplate:
            boilerplate_contaminated_docs.add(doc_id)

        # ==== Check 9: Citation graph edges ====
        for cited_id in prior_opinions:
            citation_edges.append((doc_id, cited_id))

    # Post-pass: finalize results

    # Check 3: filter to actual duplicates (hash with >1 doc)
    results.duplicate_groups = {
        h: ids for h, ids in content_hashes.items() if len(ids) > 1
    }

    # Check 8: total contaminated docs
    results.boilerplate_doc_count = len(boilerplate_contaminated_docs)

    # Check 9: build cited_by index and find most-cited
    cited_by_counter = Counter()
    for citing_id, cited_id in citation_edges:
        if cited_id not in results.cited_by:
            results.cited_by[cited_id] = []
        results.cited_by[cited_id].append(citing_id)
        cited_by_counter[cited_id] += 1

    results.most_cited = cited_by_counter.most_common(30)

    # Separate dangling targets (cited but not in corpus, even after ID normalization)
    for target_id, citing_docs in results.cited_by.items():
        if target_id not in expanded_ids:
            results.dangling_targets[target_id] = citing_docs

    # Sort outlier lists by severity
    results.low_density.sort(key=lambda x: x["ratio"])
    results.high_density.sort(key=lambda x: -x["ratio"])
    results.date_year_mismatches.sort(key=lambda x: abs(x["delta"]), reverse=True)

    return results


# =============================================================================
# Report generation
# =============================================================================

def generate_report(results: ValidationResults) -> str:
    """Generate markdown report from validation results."""
    lines = []
    w = lines.append

    w("# FPPC Corpus Validation Report")
    w(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"\nTotal documents: **{results.total_docs:,}**")

    if results.load_errors:
        w(f"\nLoad errors: **{len(results.load_errors)}**")
        for err in results.load_errors[:10]:
            w(f"  - `{err}`")

    # ---- Summary dashboard ----
    w("\n## Summary Dashboard\n")
    w("| Check | Status | Count | Severity |")
    w("|-------|--------|-------|----------|")

    dangling_count = sum(len(v) for v in results.dangling_citations.values())
    w(f"| 1. Dangling citation refs | {'PASS' if dangling_count == 0 else 'FLAG'} | "
      f"{dangling_count} refs in {len(results.dangling_citations)} docs | "
      f"{'None' if dangling_count == 0 else 'Medium'} |")

    w(f"| 2. Date/year mismatches | {'PASS' if not results.date_year_mismatches else 'FLAG'} | "
      f"{len(results.date_year_mismatches)} docs (no date: {results.no_date_count}) | "
      f"{'None' if not results.date_year_mismatches else 'High'} |")

    dup_count = sum(len(v) for v in results.duplicate_groups.values())
    w(f"| 3. Duplicate full_text | {'PASS' if not results.duplicate_groups else 'FLAG'} | "
      f"{len(results.duplicate_groups)} groups ({dup_count} docs) | "
      f"{'None' if not results.duplicate_groups else 'High'} |")

    w(f"| 4. Word/page outliers | {'PASS' if not results.low_density and not results.high_density else 'FLAG'} | "
      f"{len(results.low_density)} low, {len(results.high_density)} high | "
      f"{'None' if not results.low_density and not results.high_density else 'Medium'} |")

    w(f"| 5. Section not in full_text | {'PASS' if not results.section_not_in_text else 'FLAG'} | "
      f"{len(results.section_not_in_text)} of {results.sections_checked} checked | "
      f"{'None' if not results.section_not_in_text else 'High'} |")

    w(f"| 6. Zero-section docs | INFO | {len(results.zero_section_docs)} docs | Low |")

    w(f"| 7. No-citation docs | INFO | {len(results.no_citation_docs)} docs | Low |")

    w(f"| 8. Boilerplate contamination | {'PASS' if results.boilerplate_doc_count == 0 else 'FLAG'} | "
      f"{results.boilerplate_doc_count} docs | "
      f"{'None' if results.boilerplate_doc_count == 0 else 'Medium'} |")

    dangling_target_count = len(results.dangling_targets)
    w(f"| 9. Citation graph dangling | {'PASS' if dangling_target_count == 0 else 'INFO'} | "
      f"{dangling_target_count} targets not in corpus | Low |")

    # ---- Check 1: Citation existence ----
    w("\n---\n## Check 1: Citation Existence (prior_opinions → corpus)\n")
    w(f"Total prior_opinion references: **{results.total_prior_opinion_refs:,}**")
    w(f"Valid (target exists): **{results.valid_citation_count:,}** "
      f"({results.valid_citation_count/max(results.total_prior_opinion_refs,1)*100:.1f}%)")
    w(f"Dangling (target missing): **{dangling_count}** in **{len(results.dangling_citations)}** docs")

    if results.dangling_citations:
        w("\n### Docs with dangling citations\n")
        w("| Document | Missing References |")
        w("|----------|-------------------|")
        for doc_id, missing in sorted(results.dangling_citations.items(),
                                        key=lambda x: str(x[0] or "")):
            w(f"| {doc_id} | {', '.join(missing)} |")

    # ---- Check 2: Date/year mismatches ----
    w("\n---\n## Check 2: Date/Year Consistency\n")
    w(f"Documents with parsed date: **{results.total_docs - results.no_date_count:,}**")
    w(f"Documents without date: **{results.no_date_count:,}**")
    w(f"Mismatches (>1 year delta): **{len(results.date_year_mismatches)}**")

    if results.date_year_mismatches:
        w("\n### Date/year mismatches (sorted by severity)\n")
        w("| Document | File Year | Parsed Date | Delta |")
        w("|----------|-----------|-------------|-------|")
        for m in results.date_year_mismatches[:50]:
            w(f"| {m['doc_id']} | {m['file_year']} | {m['parsed_date']} | "
              f"{m['delta']:+d} years |")
        if len(results.date_year_mismatches) > 50:
            w(f"\n*...and {len(results.date_year_mismatches) - 50} more*")

    # ---- Check 3: Duplicate full_text ----
    w("\n---\n## Check 3: Duplicate Full Text\n")
    if results.duplicate_groups:
        w(f"Found **{len(results.duplicate_groups)}** groups of documents with identical text:\n")
        for h, ids in sorted(results.duplicate_groups.items()):
            w(f"- **Group** (hash `{h}`): {', '.join(ids)}")
    else:
        w("No duplicate full_text content detected. PASS")

    # ---- Check 4: Word/page outliers ----
    w("\n---\n## Check 4: Word/Page Density Outliers\n")
    w(f"Thresholds: <{MIN_WORDS_PER_PAGE} words/page (low), >{MAX_WORDS_PER_PAGE} words/page (high)\n")

    if results.low_density:
        w(f"### Low density ({len(results.low_density)} docs) — possible image-only pages\n")
        w("| Document | Words | Pages | Ratio | Method |")
        w("|----------|-------|-------|-------|--------|")
        for d in results.low_density[:30]:
            w(f"| {d['doc_id']} | {d['word_count']} | {d['page_count']} | "
              f"{d['ratio']} w/p | {d['method']} |")
        if len(results.low_density) > 30:
            w(f"\n*...and {len(results.low_density) - 30} more*")

    if results.high_density:
        w(f"\n### High density ({len(results.high_density)} docs) — possible extraction artifact\n")
        w("| Document | Words | Pages | Ratio | Method |")
        w("|----------|-------|-------|-------|--------|")
        for d in results.high_density[:30]:
            w(f"| {d['doc_id']} | {d['word_count']} | {d['page_count']} | "
              f"{d['ratio']} w/p | {d['method']} |")
        if len(results.high_density) > 30:
            w(f"\n*...and {len(results.high_density) - 30} more*")

    if not results.low_density and not results.high_density:
        w("No word/page density outliers detected. PASS")

    # ---- Check 5: Section-in-full-text ----
    w("\n---\n## Check 5: Section Text Not Found in Full Text\n")
    w(f"Sections checked: **{results.sections_checked:,}**")
    w(f"Mismatches: **{len(results.section_not_in_text)}**")

    if results.section_not_in_text:
        w("\n### Sections whose content doesn't appear in source text\n")
        w("| Document | Section | Method | Probe Text |")
        w("|----------|---------|--------|------------|")
        for s in results.section_not_in_text[:50]:
            w(f"| {s['doc_id']} | {s['section']} | {s['section_method']} | "
              f"`{s['probe'][:60]}` |")
        if len(results.section_not_in_text) > 50:
            w(f"\n*...and {len(results.section_not_in_text) - 50} more*")
    else:
        w("\nAll extracted sections verified against source text. PASS")

    # ---- Check 6: Zero-section categorization ----
    w("\n---\n## Check 6: Zero-Section Documents\n")
    w(f"Total docs with no extracted sections: **{len(results.zero_section_docs):,}** "
      f"({len(results.zero_section_docs)/max(results.total_docs,1)*100:.1f}%)\n")

    w("### By document type\n")
    w("| Type | Count | % of zero-section |")
    w("|------|-------|-------------------|")
    total_zero = max(len(results.zero_section_docs), 1)
    for doc_type, count in results.zero_section_by_type.most_common():
        w(f"| {doc_type} | {count} | {count/total_zero*100:.1f}% |")

    w("\n### By era\n")
    w("| Era | Count | % of zero-section |")
    w("|-----|-------|-------------------|")
    for era, count in sorted(results.zero_section_by_era.items()):
        w(f"| {era} | {count} | {count/total_zero*100:.1f}% |")

    # ---- Check 7: No-citation investigation ----
    w("\n---\n## Check 7: No-Citation Documents\n")
    w(f"Total docs with zero citations: **{len(results.no_citation_docs):,}** "
      f"({len(results.no_citation_docs)/max(results.total_docs,1)*100:.1f}%)\n")

    w("### By document type\n")
    w("| Type | Count | % of no-citation |")
    w("|------|-------|------------------|")
    total_nocite = max(len(results.no_citation_docs), 1)
    for doc_type, count in results.no_citation_by_type.most_common():
        w(f"| {doc_type} | {count} | {count/total_nocite*100:.1f}% |")

    w("\n### By era\n")
    w("| Era | Count | % of no-citation |")
    w("|-----|-------|------------------|")
    for era, count in sorted(results.no_citation_by_era.items()):
        w(f"| {era} | {count} | {count/total_nocite*100:.1f}% |")

    # ---- Check 8: Boilerplate sweep ----
    w("\n---\n## Check 8: Boilerplate Contamination\n")
    w(f"Documents with boilerplate in section/embedding fields: "
      f"**{results.boilerplate_doc_count:,}** "
      f"({results.boilerplate_doc_count/max(results.total_docs,1)*100:.1f}%)\n")

    if results.boilerplate_field_counts:
        w("### Hits by field\n")
        w("| Field | Hits |")
        w("|-------|------|")
        for field_name, count in results.boilerplate_field_counts.most_common():
            w(f"| {field_name} | {count} |")

    if results.boilerplate_hits:
        w("\n### Hits by pattern\n")
        w("| Pattern | Hits | Example |")
        w("|---------|------|---------|")
        for pattern_name in sorted(results.boilerplate_hits.keys(),
                                    key=lambda k: len(results.boilerplate_hits[k]),
                                    reverse=True):
            hits = results.boilerplate_hits[pattern_name]
            display_name = BOILERPLATE_NAMES.get(pattern_name, pattern_name)
            example = hits[0]["snippet"][:60] if hits else ""
            w(f"| {display_name} | {len(hits)}{'+'  if len(hits) >= 50 else ''} | "
              f"`{example}` |")

    # ---- Check 9: Citation graph ----
    w("\n---\n## Check 9: Citation Graph\n")
    w(f"Unique documents cited by others: **{len(results.cited_by):,}**")
    w(f"Dangling targets (cited but not in corpus): **{len(results.dangling_targets):,}**\n")

    w("### Most-cited documents (top 30)\n")
    w("| Document | Times Cited | In Corpus? |")
    w("|----------|-------------|------------|")
    for cited_id, count in results.most_cited:
        in_corpus = "Yes" if cited_id not in results.dangling_targets else "**NO**"
        w(f"| {cited_id} | {count} | {in_corpus} |")

    if results.dangling_targets:
        w("\n### Dangling citation targets (most-referenced first)\n")
        w("| Target ID | Cited By (count) | Example Citing Docs |")
        w("|-----------|-----------------|---------------------|")
        sorted_dangling = sorted(results.dangling_targets.items(),
                                  key=lambda x: len(x[1]), reverse=True)
        for target_id, citing_docs in sorted_dangling[:50]:
            examples = ", ".join(citing_docs[:5])
            if len(citing_docs) > 5:
                examples += f" (+{len(citing_docs)-5} more)"
            w(f"| {target_id} | {len(citing_docs)} | {examples} |")
        if len(sorted_dangling) > 50:
            w(f"\n*...and {len(sorted_dangling) - 50} more dangling targets*")

    # ---- Actionable summary ----
    w("\n---\n## Actionable Summary\n")

    issues = []
    if results.date_year_mismatches:
        issues.append(f"- **{len(results.date_year_mismatches)} date/year mismatches** — "
                      "investigate for possible file/metadata errors")
    if results.duplicate_groups:
        issues.append(f"- **{len(results.duplicate_groups)} duplicate text groups** — "
                      "investigate for download/extraction mixups")
    if results.section_not_in_text:
        issues.append(f"- **{len(results.section_not_in_text)} sections not matching source** — "
                      "high priority for legal accuracy")
    if results.boilerplate_doc_count > 0:
        issues.append(f"- **{results.boilerplate_doc_count} docs with boilerplate** in sections — "
                      "consider re-running cleanup")
    if results.dangling_targets:
        issues.append(f"- **{len(results.dangling_targets)} cited documents not in corpus** — "
                      "may be real gaps or OCR errors")

    if issues:
        w("### Issues requiring attention\n")
        for issue in issues:
            w(issue)
    else:
        w("No critical issues found. Corpus passes all checks.")

    w("\n### Informational\n")
    w(f"- {len(results.zero_section_docs)} docs without sections "
      f"(mostly {results.zero_section_by_type.most_common(1)[0][0] if results.zero_section_by_type else 'unknown'} type)")
    w(f"- {len(results.no_citation_docs)} docs without any citations")
    w(f"- {len(results.cited_by)} documents referenced by other letters")
    if results.most_cited:
        top = results.most_cited[0]
        w(f"- Most-cited document: {top[0]} ({top[1]} citations)")

    return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Comprehensive FPPC corpus validation")
    parser.add_argument("--json", action="store_true", help="Also write JSON report")
    args = parser.parse_args()

    print("Loading all documents...")
    documents, known_ids, load_errors = load_all_documents(EXTRACTED_DIR)
    print(f"  Loaded {len(documents):,} documents ({len(load_errors)} errors)")

    print("Running validation checks...")
    results = run_all_checks(documents, known_ids)
    results.load_errors = load_errors

    print("Generating report...")
    report = generate_report(results)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    with open(REPORT_PATH, "w") as f:
        f.write(report)
    print(f"  Report written to {REPORT_PATH}")

    if args.json:
        # Write machine-readable subset (skip large fields like cited_by full lists)
        json_data = {
            "generated_at": datetime.now().isoformat(),
            "total_docs": results.total_docs,
            "checks": {
                "1_citation_existence": {
                    "total_refs": results.total_prior_opinion_refs,
                    "valid": results.valid_citation_count,
                    "dangling_count": sum(len(v) for v in results.dangling_citations.values()),
                    "dangling_docs": len(results.dangling_citations),
                    "dangling_details": results.dangling_citations,
                },
                "2_date_year": {
                    "mismatches": len(results.date_year_mismatches),
                    "no_date": results.no_date_count,
                    "details": results.date_year_mismatches,
                },
                "3_duplicates": {
                    "groups": len(results.duplicate_groups),
                    "details": results.duplicate_groups,
                },
                "4_word_page_outliers": {
                    "low_density": len(results.low_density),
                    "high_density": len(results.high_density),
                    "low_details": results.low_density[:50],
                    "high_details": results.high_density[:50],
                },
                "5_section_not_in_text": {
                    "mismatches": len(results.section_not_in_text),
                    "checked": results.sections_checked,
                    "details": results.section_not_in_text,
                },
                "6_zero_sections": {
                    "count": len(results.zero_section_docs),
                    "by_type": dict(results.zero_section_by_type),
                    "by_era": dict(results.zero_section_by_era),
                },
                "7_no_citations": {
                    "count": len(results.no_citation_docs),
                    "by_type": dict(results.no_citation_by_type),
                    "by_era": dict(results.no_citation_by_era),
                },
                "8_boilerplate": {
                    "contaminated_docs": results.boilerplate_doc_count,
                    "field_counts": dict(results.boilerplate_field_counts),
                    "pattern_counts": {k: len(v) for k, v in results.boilerplate_hits.items()},
                },
                "9_citation_graph": {
                    "unique_cited": len(results.cited_by),
                    "dangling_targets": len(results.dangling_targets),
                    "most_cited": [{"id": id, "count": c} for id, c in results.most_cited],
                    "dangling_target_ids": {k: len(v) for k, v in results.dangling_targets.items()},
                },
            },
        }
        with open(JSON_REPORT_PATH, "w") as f:
            json.dump(json_data, f, indent=2)
        print(f"  JSON report written to {JSON_REPORT_PATH}")

    # Print summary to console
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    dangling = sum(len(v) for v in results.dangling_citations.values())
    checks = [
        ("1. Citation existence", dangling == 0, f"{dangling} dangling refs"),
        ("2. Date/year consistency", not results.date_year_mismatches,
         f"{len(results.date_year_mismatches)} mismatches"),
        ("3. Duplicate detection", not results.duplicate_groups,
         f"{len(results.duplicate_groups)} groups"),
        ("4. Word/page density", not results.low_density and not results.high_density,
         f"{len(results.low_density)} low, {len(results.high_density)} high"),
        ("5. Section verification", not results.section_not_in_text,
         f"{len(results.section_not_in_text)} mismatches"),
        ("6. Zero-section docs", True, f"{len(results.zero_section_docs)} docs (info)"),
        ("7. No-citation docs", True, f"{len(results.no_citation_docs)} docs (info)"),
        ("8. Boilerplate sweep", results.boilerplate_doc_count == 0,
         f"{results.boilerplate_doc_count} docs"),
        ("9. Citation graph", True, f"{len(results.dangling_targets)} dangling targets (info)"),
    ]

    for name, passed, detail in checks:
        status = "PASS" if passed else "FLAG"
        print(f"  [{status:4s}] {name}: {detail}")

    print(f"\nFull report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
