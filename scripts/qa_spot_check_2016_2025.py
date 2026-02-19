#!/usr/bin/env python3
"""QA spot-check for 2016-2025 era extracted JSON files against source PDFs."""

import json
import os
import re
import sys

import fitz  # pymupdf

# Documents to review
DOCS = [
    {"json": "data/extracted/2017/17-122.json", "pdf": "raw_pdfs/2017/17-122.pdf"},
    {"json": "data/extracted/2017/17-090W.json", "pdf": "raw_pdfs/2017/17-090W.pdf"},
    {"json": "data/extracted/2024/A-24-018.json", "pdf": "raw_pdfs/2024/24018.pdf"},
    {"json": "data/extracted/2016/16-072.json", "pdf": "raw_pdfs/2016/16-072.pdf"},
    {"json": "data/extracted/2023/A-23-121.json", "pdf": "raw_pdfs/2023/23121.pdf"},
    {"json": "data/extracted/2017/17-259.json", "pdf": "raw_pdfs/2017/17-259.pdf"},
    {"json": "data/extracted/2024/A-24-093.json", "pdf": "raw_pdfs/2024/24093.pdf"},
    {"json": "data/extracted/2020/A-20-085.json", "pdf": "raw_pdfs/2020/Final A-20-085.pdf"},
    {"json": "data/extracted/2019/A-18-275.json", "pdf": "raw_pdfs/2019/18275-1090pdf.pdf"},
    {"json": "data/extracted/2025/A-25-133.json", "pdf": "raw_pdfs/2025/25133.pdf"},
    {"json": "data/extracted/2017/17-175W.json", "pdf": "raw_pdfs/2017/17-175W.pdf"},
    {"json": "data/extracted/2016/16-036.json", "pdf": "raw_pdfs/2016/16-036.pdf"},
    {"json": "data/extracted/2017/17-040.json", "pdf": "raw_pdfs/2017/17-040.pdf"},
    {"json": "data/extracted/2018/A-18-031.json", "pdf": "raw_pdfs/2018/18031-1090pdf.pdf"},
    {"json": "data/extracted/2016/16-126.json", "pdf": "raw_pdfs/2016/16-126.pdf"},
    {"json": "data/extracted/2023/I-23-081.json", "pdf": "raw_pdfs/2023/23081.pdf"},
    {"json": "data/extracted/2021/A-21-136.json", "pdf": "raw_pdfs/2021/21136.pdf"},
    {"json": "data/extracted/2017/17-108.json", "pdf": "raw_pdfs/2017/17-108.pdf"},
    {"json": "data/extracted/2017/17-053.json", "pdf": "raw_pdfs/2017/17-053.pdf"},
    {"json": "data/extracted/2025/A-25-089.json", "pdf": "raw_pdfs/2025/25089.pdf"},
]

BASE_DIR = "/home/nick/Projects/fppc_scrape"

# Page header patterns
PAGE_HEADER_PATTERNS = [
    r"File No\.\s+[\w-]+\s*\n\s*Page No\.\s+\d+",
    r"Page\s+\d+\s+of\s+\d+",
    r"^File No\.\s+",
    r"^Page No\.\s+\d+",
    r"^Our File No\.",
]

# Boilerplate / closing patterns
CLOSING_BOILERPLATE = [
    r"If you have (?:other )?questions? on this matter",
    r"Should you have (?:any )?(?:other )?questions?",
    r"please (?:do not hesitate to )?contact me",
    r"Sincerely,",
    r"^By:\s*$",
    r"General Counsel",
    r"Legal Division",
    r"Counsel,\s+Legal Division",
    r"Assistant General Counsel",
    r"^\w+:\w+$",  # initials like MFC:jgl, TL:aja
]

# Self-citation patterns
SELF_CITE_PATTERNS = [
    r"The Political Reform Act is contained in Government Code",
    r"All statutory\s+references are to the Government Code",
    r"All regulatory\s+references are to Title 2",
    r"The regulations of the Fair Political Practices",
    r"Government Code [Ss]ections 81000 through 91014",
    r"Commission regulations appear at Title 2",
]

# Footnote leak patterns
FOOTNOTE_PATTERNS = [
    r"^\d+\s+(?:The Political Reform Act|Government Code|All statutory|All regulatory)",
]

# Date patterns for extraction from PDF
DATE_PATTERN = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(\d{4})"
)

MONTH_MAP = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12"
}


def extract_pdf_text(pdf_path):
    """Extract text from PDF using pymupdf."""
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except Exception as e:
        return f"ERROR: {e}"


def find_date_in_pdf(pdf_text):
    """Find the first date in PDF text."""
    match = DATE_PATTERN.search(pdf_text)
    if match:
        month, day, year = match.group(1), match.group(2), match.group(3)
        return f"{year}-{MONTH_MAP[month]}-{int(day):02d}"
    return None


def check_text_in_pdf(section_text, pdf_text, tolerance=50):
    """Check if section text first N chars appear in PDF text (fuzzy)."""
    if not section_text:
        return True
    # Normalize whitespace
    clean_section = re.sub(r'\s+', ' ', section_text[:tolerance]).strip()
    clean_pdf = re.sub(r'\s+', ' ', pdf_text).strip()
    return clean_section[:30] in clean_pdf


def check_for_pattern(text, patterns, label=""):
    """Check if any pattern matches in text."""
    if not text:
        return []
    issues = []
    for pat in patterns:
        matches = re.findall(pat, text, re.MULTILINE | re.IGNORECASE)
        if matches:
            for m in matches:
                sample = m if isinstance(m, str) else str(m)
                issues.append(f"{label}: matched '{pat}' -> '{sample[:80]}'")
    return issues


def check_page_headers(text, section_name):
    """Check for page headers in section text."""
    if not text:
        return []
    issues = []
    for pat in PAGE_HEADER_PATTERNS:
        matches = re.findall(pat, text, re.MULTILINE)
        if matches:
            for m in matches:
                issues.append(f"PAGE_HEADER in {section_name}: '{m[:60]}'")
    return issues


def check_closing_boilerplate(text, section_name):
    """Check for closing boilerplate in section text."""
    if not text:
        return []
    issues = []
    for pat in CLOSING_BOILERPLATE:
        matches = re.findall(pat, text, re.MULTILINE | re.IGNORECASE)
        if matches:
            for m in matches:
                issues.append(f"BOILERPLATE in {section_name}: '{m[:80]}'")
    return issues


def check_self_citation(text, section_name):
    """Check for self-citation leak in section text."""
    if not text:
        return []
    issues = []
    for pat in SELF_CITE_PATTERNS:
        matches = re.findall(pat, text, re.MULTILINE | re.IGNORECASE)
        if matches:
            for m in matches:
                issues.append(f"SELF_CITE in {section_name}: '{m[:80]}'")
    return issues


def check_footnote_leak(text, section_name):
    """Check for footnote content leaked into section text."""
    if not text:
        return []
    issues = []
    for pat in FOOTNOTE_PATTERNS:
        matches = re.findall(pat, text, re.MULTILINE)
        if matches:
            for m in matches:
                issues.append(f"FOOTNOTE_LEAK in {section_name}: '{m[:80]}'")
    return issues


def analyze_document(doc_info):
    """Analyze a single document."""
    json_path = os.path.join(BASE_DIR, doc_info["json"])
    pdf_path = os.path.join(BASE_DIR, doc_info["pdf"])

    result = {
        "id": None,
        "json_path": doc_info["json"],
        "pdf_path": doc_info["pdf"],
        "issues": [],
        "warnings": [],
        "is_withdrawal": False,
        "is_standard": True,
        "sections_present": {
            "question": False,
            "conclusion": False,
            "facts": False,
            "analysis": False,
        },
    }

    # 1. Check JSON exists and parses
    if not os.path.exists(json_path):
        result["issues"].append("JSON file does not exist")
        return result

    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        result["issues"].append(f"JSON parse error: {e}")
        return result

    result["id"] = data.get("id", "UNKNOWN")

    # 2. Check PDF exists
    if not os.path.exists(pdf_path):
        result["issues"].append("PDF file does not exist")
        return result

    # 3. Extract PDF text
    pdf_text = extract_pdf_text(pdf_path)
    if pdf_text.startswith("ERROR"):
        result["issues"].append(f"PDF extraction failed: {pdf_text}")
        return result

    # Detect withdrawal
    doc_id = data.get("id", "")
    if doc_id.endswith("W") or "W-" in data.get("content", {}).get("full_text", "")[:500]:
        result["is_withdrawal"] = True

    # Check document_type
    doc_type = data.get("parsed", {}).get("document_type", "")
    if result["is_withdrawal"] and doc_type != "correspondence":
        result["issues"].append(
            f"DOCUMENT_TYPE: withdrawal should be 'correspondence', got '{doc_type}'"
        )

    # Check for non-standard format (I- prefix = informal, may have different structure)
    has_standard_format = data.get("sections", {}).get("has_standard_format", True)

    # Sections
    sections = data.get("sections", {})

    # Check each section
    for sec_name in ["question", "conclusion", "facts", "analysis"]:
        sec_text = sections.get(sec_name)
        if sec_text and len(sec_text.strip()) > 5:
            result["sections_present"][sec_name] = True

            # Check section text appears in PDF
            if not check_text_in_pdf(sec_text, pdf_text):
                result["issues"].append(
                    f"CONTENT_MISMATCH: {sec_name} text not found in PDF"
                )

            # Check for page headers
            result["issues"].extend(check_page_headers(sec_text, sec_name))

            # Check for closing boilerplate
            result["issues"].extend(check_closing_boilerplate(sec_text, sec_name))

            # Check for self-citation leaks
            result["issues"].extend(check_self_citation(sec_text, sec_name))

            # Check for footnote leaks
            result["issues"].extend(check_footnote_leak(sec_text, sec_name))

    # Special checks for question section
    question = sections.get("question", "")
    if question:
        # Check if question starts with garbage (truncated header parsing)
        if question and question[:5] in ("S AND", "S\nThe", "ION\n", "IONS\n"):
            result["issues"].append(
                f"QUESTION_PARSE_ERROR: starts with garbage '{question[:30]}'"
            )

    # Special checks for conclusion section
    conclusion = sections.get("conclusion", "")
    if conclusion:
        # Check clean ending — last 200 chars shouldn't have signature
        last_200 = conclusion[-200:] if len(conclusion) > 200 else conclusion
        for pat in [r"Sincerely", r"General Counsel", r"Legal Division"]:
            if re.search(pat, last_200, re.IGNORECASE):
                result["issues"].append(f"CONCLUSION_DIRTY_END: contains '{pat}'")

        # Check for footnote contamination in conclusion
        footnote_pattern = r'\d+\s+(?:Informal|The Political|Government Code|Pursuant)'
        matches = re.findall(footnote_pattern, conclusion)
        if matches:
            for m in matches:
                result["issues"].append(f"CONCLUSION_FOOTNOTE: '{m[:60]}'")

    # Special checks for analysis section
    analysis = sections.get("analysis", "")
    if analysis:
        # Check analysis doesn't end with signature/boilerplate
        last_300 = analysis[-300:] if len(analysis) > 300 else analysis
        for pat in [r"Sincerely", r"General Counsel", r"Legal Division", r"Counsel,\s+Legal"]:
            if re.search(pat, last_300, re.IGNORECASE):
                result["issues"].append(f"ANALYSIS_DIRTY_END: contains '{pat}'")

    # Date check
    parsed_date = data.get("parsed", {}).get("date")
    if parsed_date:
        pdf_date = find_date_in_pdf(pdf_text)
        if pdf_date and parsed_date != pdf_date:
            result["issues"].append(
                f"DATE_MISMATCH: JSON='{parsed_date}' vs PDF='{pdf_date}'"
            )
    else:
        result["warnings"].append("No date extracted")

    # Citations check
    citations = data.get("citations", {})
    gov_codes = citations.get("government_code", [])
    regs = citations.get("regulations", [])

    # Check if 81000 is in citations for standard letters (it should almost always be)
    if not result["is_withdrawal"] and has_standard_format:
        if "81000" not in gov_codes and len(gov_codes) == 0:
            result["warnings"].append("No government code citations found")

    # Check for self-citation in prior_opinions
    prior_ops = citations.get("prior_opinions", [])
    for op in prior_ops:
        # Strip letter prefix for comparison
        clean_op = re.sub(r'^[AI]-', '', op)
        clean_id = re.sub(r'^[AI]-', '', doc_id)
        if clean_op == clean_id:
            result["issues"].append(f"SELF_CITE_IN_PRIOR_OPS: {op}")

    # Check for non-standard format detection
    # "QUESTIONS AND CONCLUSIONS" merged format (like A-24-018)
    if "QUESTIONS AND CONCLUSIONS" in pdf_text:
        result["is_standard"] = True  # it's a valid format, just non-standard
        if not sections.get("question"):
            result["warnings"].append("Non-standard 'QUESTIONS AND CONCLUSIONS' format, no question extracted")
        # This format merges Q&A — check if parsed correctly
        if sections.get("question") and not sections.get("conclusion"):
            result["warnings"].append("QUESTIONS AND CONCLUSIONS format: question present but conclusion missing (may be merged)")

    # Check if there's a "QUESTION" header in PDF but no question extracted
    if re.search(r'\bQUESTION\b', pdf_text) and not sections.get("question"):
        if not result["is_withdrawal"]:
            result["issues"].append("MISSING_QUESTION: PDF has QUESTION header but no question extracted")

    # Check if there's a "CONCLUSION" header in PDF but no conclusion extracted
    if re.search(r'\bCONCLUSION\b', pdf_text) and not sections.get("conclusion"):
        if not result["is_withdrawal"]:
            result["issues"].append("MISSING_CONCLUSION: PDF has CONCLUSION header but no conclusion extracted")

    # Check if there's a "FACTS" header in PDF but no facts extracted
    if re.search(r'\bFACTS\b', pdf_text) and not sections.get("facts"):
        if not result["is_withdrawal"]:
            result["issues"].append("MISSING_FACTS: PDF has FACTS header but no facts extracted")

    # Check if there's an "ANALYSIS" header in PDF but no analysis extracted
    if re.search(r'\bANALYSIS\b', pdf_text) and not sections.get("analysis"):
        if not result["is_withdrawal"]:
            result["issues"].append("MISSING_ANALYSIS: PDF has ANALYSIS header but no analysis extracted")

    # Extraction confidence check
    confidence = sections.get("extraction_confidence", 0)
    if confidence < 0.5 and not result["is_withdrawal"]:
        result["warnings"].append(f"Low extraction confidence: {confidence}")

    # Check for incomplete analysis (cut off mid-sentence)
    if analysis:
        last_char = analysis.rstrip()[-1] if analysis.rstrip() else ""
        if last_char not in ".)?!\"'":
            result["warnings"].append(
                f"ANALYSIS_CUTOFF: ends with '{analysis.rstrip()[-30:]}'"
            )

    # Store extra info for reporting
    result["doc_type"] = doc_type
    result["word_count"] = data.get("extraction", {}).get("word_count", 0)
    result["page_count"] = data.get("extraction", {}).get("page_count", 0)
    result["extraction_method"] = sections.get("extraction_method", "")
    result["confidence"] = confidence

    return result


def main():
    print("=" * 80)
    print("QA SPOT CHECK: 2016-2025 ERA DOCUMENTS (20 docs)")
    print("=" * 80)
    print()

    results = []
    for doc_info in DOCS:
        result = analyze_document(doc_info)
        results.append(result)

    # Print per-document results
    for r in results:
        status = "PASS" if not r["issues"] else "FAIL"
        warn_count = len(r["warnings"])
        issue_count = len(r["issues"])
        withdrawal_tag = " [WITHDRAWAL]" if r["is_withdrawal"] else ""

        sections_str = ""
        for s in ["question", "conclusion", "facts", "analysis"]:
            sections_str += ("+" if r["sections_present"][s] else "-") + s[0].upper()

        print(f"\n{'='*60}")
        print(f"[{status}] {r['id']}{withdrawal_tag}")
        print(f"  Type: {r.get('doc_type', '?')} | Sections: {sections_str} | "
              f"Confidence: {r.get('confidence', '?')}")
        print(f"  Pages: {r.get('page_count', '?')} | Words: {r.get('word_count', '?')}")
        print(f"  Method: {r.get('extraction_method', '?')}")

        if r["issues"]:
            print(f"  ISSUES ({issue_count}):")
            for issue in r["issues"]:
                print(f"    [!] {issue}")
        if r["warnings"]:
            print(f"  WARNINGS ({warn_count}):")
            for w in r["warnings"]:
                print(f"    [?] {w}")
        if not r["issues"] and not r["warnings"]:
            print("  All checks passed.")

    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    total = len(results)
    withdrawals = [r for r in results if r["is_withdrawal"]]
    standard = [r for r in results if not r["is_withdrawal"]]
    passed = [r for r in results if not r["issues"]]
    failed = [r for r in results if r["issues"]]

    print(f"\nTotal documents: {total}")
    print(f"  Standard advice letters: {len(standard)}")
    print(f"  Withdrawals/correspondence: {len(withdrawals)}")
    print(f"\nPassed: {len(passed)}/{total}")
    print(f"Failed: {len(failed)}/{total}")

    # Section detection rates (standard letters only)
    print(f"\nSECTION DETECTION RATES (standard letters, n={len(standard)}):")
    for sec in ["question", "conclusion", "facts", "analysis"]:
        count = sum(1 for r in standard if r["sections_present"][sec])
        pct = count / len(standard) * 100 if standard else 0
        print(f"  {sec:12s}: {count}/{len(standard)} ({pct:.0f}%)")

    # Issue counts by type
    print("\nISSUE COUNTS BY TYPE:")
    issue_types = {}
    for r in results:
        for issue in r["issues"]:
            # Extract type from "TYPE: details" or "TYPE in section: details"
            itype = issue.split(":")[0].split(" in ")[0].strip()
            issue_types[itype] = issue_types.get(itype, 0) + 1
    for itype, count in sorted(issue_types.items(), key=lambda x: -x[1]):
        print(f"  {itype:30s}: {count}")

    # Warning counts
    print("\nWARNING COUNTS BY TYPE:")
    warn_types = {}
    for r in results:
        for w in r["warnings"]:
            wtype = w.split(":")[0].strip()
            warn_types[wtype] = warn_types.get(wtype, 0) + 1
    for wtype, count in sorted(warn_types.items(), key=lambda x: -x[1]):
        print(f"  {wtype:30s}: {count}")

    # Problem documents detail
    if failed:
        print(f"\nPROBLEMATIC DOCUMENTS ({len(failed)}):")
        for r in failed:
            print(f"\n  {r['id']} ({r['json_path']}):")
            for issue in r["issues"]:
                print(f"    - {issue}")

    print("\n" + "=" * 80)
    print("END OF QA REPORT")
    print("=" * 80)


if __name__ == "__main__":
    main()
