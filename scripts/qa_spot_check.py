#!/usr/bin/env python3
"""QA spot-check script for extracted JSON files against source PDFs.

Checks 15 documents from the 1975-1995 era for extraction quality issues.
"""

import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import fitz  # pymupdf
except ImportError:
    print("ERROR: pymupdf not installed. Run: pip install pymupdf")
    sys.exit(1)


# Documents to check: (json_path, pdf_path)
DOCS = [
    # 1975-1985 (5 docs)
    ("data/extracted/1983/83A281.json", "raw_pdfs/1983/83A281.PDF"),
    ("data/extracted/1976/76569.json", "raw_pdfs/1976/76569.pdf"),
    ("data/extracted/1976/76403.json", "raw_pdfs/1976/76403.pdf"),
    ("data/extracted/1980/80A004.json", "raw_pdfs/1980/80A004.PDF"),
    ("data/extracted/1976/76038.json", "raw_pdfs/1976/76038.PDF"),
    # 1986-1995 (10 docs)
    ("data/extracted/1994/94-148.json", "raw_pdfs/1994/94-148.pdf"),
    ("data/extracted/1995/95-031.json", "raw_pdfs/1995/95-031.pdf"),
    ("data/extracted/1990/90660.json", "raw_pdfs/1990/90660.pdf"),
    ("data/extracted/1986/86182.json", "raw_pdfs/1986/86182.PDF"),
    ("data/extracted/1991/UNK-91-10339.json", "raw_pdfs/1991/91-546.pdf"),
    ("data/extracted/1990/90276.json", "raw_pdfs/1990/90276.pdf"),
    ("data/extracted/1990/90-612.json", "raw_pdfs/1990/90-612.pdf"),
    ("data/extracted/1986/86127.json", "raw_pdfs/1986/86127.PDF"),
    ("data/extracted/1990/90-639.json", "raw_pdfs/1990/90-639.pdf"),
    ("data/extracted/1991/UNK-91-10246.json", "raw_pdfs/1991/91047.pdf"),
]


def extract_pdf_text(pdf_path: str) -> tuple[str, int]:
    """Extract text from PDF using pymupdf. Returns (text, page_count)."""
    full_path = PROJECT_ROOT / pdf_path
    if not full_path.exists():
        return "", 0
    doc = fitz.open(str(full_path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    page_count = len(pages)
    doc.close()
    return "\n\n".join(pages), page_count


def find_date_in_pdf(pdf_text: str) -> list[str]:
    """Find date-like strings in PDF text."""
    # Match patterns like "January 15, 1994" or "March 16, 1992"
    month_pattern = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    date_pattern = rf"({month_pattern}\s+\d{{1,2}},?\s+\d{{4}})"
    matches = re.findall(date_pattern, pdf_text, re.IGNORECASE)
    return matches


def check_boilerplate_in_section(text: str, section_name: str) -> list[str]:
    """Check for known boilerplate contamination in sections."""
    issues = []
    if not text:
        return issues

    # Footnote leak: "Informal assistance does not provide..."
    if re.search(r"[Ii]nformal\s+assist\w+\s+does\s+not\s+provide", text):
        issues.append(f"{section_name}: Contains 'Informal assistance does not provide...' footnote leak")

    # Statutory reference boilerplate: "Government Code Sections 81000-91015"
    if re.search(r"Government\s+[Cc]ode\s+[Ss]ections?\s+81?0?00[\-–]9101[05]", text):
        issues.append(f"{section_name}: Contains statutory reference boilerplate (Gov Code 81000-91015)")

    # "All statutory references are to the Government Code"
    if re.search(r"All\s+statutory\s+refer", text):
        issues.append(f"{section_name}: Contains 'All statutory references...' boilerplate")

    # "All regulatory references are to"
    if re.search(r"All\s+(?:regulatory\s+)?references?\s+(?:are\s+)?to\s+(?:the\s+)?(?:Government|Title|regulations)", text):
        issues.append(f"{section_name}: Contains 'All references are to...' boilerplate")

    # Commission regulations appear at 2 California Code of Regulations
    if re.search(r"Commission\s+regulations\s+appear\s+at", text):
        issues.append(f"{section_name}: Contains 'Commission regulations appear at...' boilerplate")

    # Closing boilerplate: "If you have any questions"
    if re.search(r"If\s+you\s+have\s+any\s+(?:additional\s+|further\s+)?questions", text):
        issues.append(f"{section_name}: Contains closing boilerplate 'If you have any questions...'")

    # Signature block
    if re.search(r"[Ss]incerely,?", text):
        issues.append(f"{section_name}: Contains 'Sincerely' (signature block leak)")

    # Phone number (916) 322-
    if re.search(r"\(916\)\s*322[\-–·]", text):
        issues.append(f"{section_name}: Contains FPPC phone number (916) 322-XXXX")

    return issues


def check_page_header_contamination(text: str, section_name: str) -> list[str]:
    """Check for page header contamination in sections."""
    issues = []
    if not text:
        return issues

    # "File No. A-..." or "Our File No. ..."
    if re.search(r"(?:Our\s+)?File\s+No\.\s+(?:A-|I-|1-)?\d", text):
        issues.append(f"{section_name}: Contains 'File No.' page header contamination")

    # "Page Two", "Page 2", "Page Three", etc.
    if re.search(r"Page\s+(?:Two|Three|Four|Five|2|3|4|5)\b", text, re.IGNORECASE):
        issues.append(f"{section_name}: Contains page number header ('Page Two/Three/...')")

    # FPPC address line
    if re.search(r"428\s+J\s+Street", text):
        issues.append(f"{section_name}: Contains FPPC address '428 J Street'")

    # P.O. Box 807
    if re.search(r"P\.?O\.?\s+Box\s+807", text):
        issues.append(f"{section_name}: Contains 'P.O. Box 807' header")

    return issues


def check_ocr_garbage(text: str, section_name: str) -> list[str]:
    """Check for OCR garbage characters in sections."""
    issues = []
    if not text:
        return issues

    # Japanese/CJK characters (common OCR artifact from poor scans)
    cjk_pattern = re.compile(r'[\u3000-\u9fff\uff00-\uffef]')
    cjk_matches = cjk_pattern.findall(text)
    if len(cjk_matches) > 2:
        issues.append(f"{section_name}: Contains {len(cjk_matches)} CJK/Japanese characters (OCR garbage)")

    # Runs of garbled chars: consecutive special chars or pattern like "::::::"
    if re.search(r'[:;]{5,}', text):
        issues.append(f"{section_name}: Contains runs of colons/semicolons (OCR garbage)")

    # Black squares / blocks ■
    block_count = text.count('■')
    if block_count > 2:
        issues.append(f"{section_name}: Contains {block_count} block characters '■' (OCR artifacts)")

    # Words without spaces (concatenated OCR): long runs of lowercase without spaces
    long_words = re.findall(r'[a-z]{30,}', text)
    if long_words:
        issues.append(f"{section_name}: Contains concatenated words without spaces (OCR): '{long_words[0][:50]}...'")

    return issues


def check_citations(citations: dict, full_text: str) -> list[str]:
    """Check if citations look valid."""
    issues = []

    gov_codes = citations.get("government_code", [])
    for code in gov_codes:
        # Extract numeric part
        num_match = re.match(r'(\d+)', code)
        if num_match:
            num = int(num_match.group(1))
            # Valid ranges: 81000-92000 (Political Reform Act) or 1090-1097 (conflicts)
            if not ((81000 <= num <= 92000) or (1090 <= num <= 1097)):
                # Check if it's a section from PRA like 82013, 84200 etc - those are fine
                # Flag truly out-of-range ones
                issues.append(f"Citation: Gov Code {code} is outside expected range (81000-92000, 1090-1097)")

    # Check for self-citation leak (own file number in prior_opinions)
    prior_ops = citations.get("prior_opinions", [])
    for op in prior_ops:
        # Self-cite if the prior opinion matches the doc's own file number
        # We'll check this at the doc level
        pass

    return issues


def check_document_type(doc_data: dict, full_text: str, pdf_text: str) -> list[str]:
    """Check if document_type classification is reasonable."""
    issues = []
    doc_type = doc_data.get("parsed", {}).get("document_type", "unknown")

    text_lower = (pdf_text or full_text).lower()

    # Check if classified as "advice_letter" but contains "informal assistance" language
    if doc_type == "advice_letter" and re.search(r"informal\s+assistance", text_lower):
        if re.search(r"request\s+for\s+informal\s+assistance", text_lower):
            issues.append(f"document_type: Classified as 'advice_letter' but document says 'Request for Informal Assistance' - should be 'informal_advice'")

    # Check if classified as "correspondence" but has standard advice letter format
    if doc_type == "correspondence":
        has_question = bool(re.search(r'\bQUESTION\b', text_lower) or re.search(r'\bquestion\b', text_lower))
        has_conclusion = bool(re.search(r'\bCONCLUSION\b', text_lower) or re.search(r'\bconclusion\b', text_lower))
        if has_question and has_conclusion:
            issues.append(f"document_type: Classified as 'correspondence' but has QUESTION/CONCLUSION headers - may be advice_letter")

    # Check if it's a memo (not a letter)
    if re.search(r'\bMemorandum\b|\bMemo\b', pdf_text or full_text, re.IGNORECASE):
        if doc_type not in ("memorandum", "correspondence", "memo"):
            issues.append(f"document_type: Document appears to be a memorandum but classified as '{doc_type}'")

    return issues


def check_date(doc_data: dict, pdf_text: str) -> list[str]:
    """Check if the extracted date matches dates found in the PDF."""
    issues = []

    parsed_date = doc_data.get("parsed", {}).get("date")
    date_raw = doc_data.get("parsed", {}).get("date_raw")

    pdf_dates = find_date_in_pdf(pdf_text)

    if not parsed_date and pdf_dates:
        issues.append(f"date: No date extracted but PDF contains date(s): {pdf_dates[:3]}")
    elif parsed_date and pdf_dates:
        # Try to match the extracted date to a PDF date
        try:
            parsed_dt = datetime.strptime(parsed_date, "%Y-%m-%d")
            matched = False
            for pdf_date_str in pdf_dates:
                # Normalize: remove extra spaces, commas
                clean = re.sub(r'\s+', ' ', pdf_date_str).strip()
                for fmt in ["%B %d, %Y", "%B %d %Y"]:
                    try:
                        pdf_dt = datetime.strptime(clean, fmt)
                        if parsed_dt == pdf_dt:
                            matched = True
                            break
                    except ValueError:
                        continue
                if matched:
                    break
            if not matched:
                issues.append(f"date: Extracted date '{parsed_date}' does not match any PDF date: {pdf_dates[:3]}")
        except ValueError:
            issues.append(f"date: Could not parse extracted date '{parsed_date}'")

    return issues


def check_analysis_ending(analysis_text: str) -> list[str]:
    """Check if analysis section ends cleanly."""
    issues = []
    if not analysis_text:
        return issues

    # Check last 200 chars for signature block
    tail = analysis_text[-300:]

    if re.search(r"[Ss]incerely", tail):
        issues.append("analysis: Ends with 'Sincerely' (signature block not stripped)")

    if re.search(r"If\s+you\s+have\s+any\s+(?:additional\s+|further\s+)?questions", tail):
        issues.append("analysis: Ends with closing boilerplate 'If you have any questions'")

    # Check for list of enclosures
    if re.search(r"Enclos(?:ure|ed)", tail):
        issues.append("analysis: Ends with 'Enclosure' (attachment list not stripped)")

    return issues


def check_self_cite(doc_data: dict) -> list[str]:
    """Check if the document cites itself as a prior opinion."""
    issues = []
    doc_id = doc_data.get("id", "")
    prior_ops = doc_data.get("citations", {}).get("prior_opinions", [])

    for op in prior_ops:
        # Normalize both for comparison
        op_clean = re.sub(r'[^a-zA-Z0-9]', '', op).lower()
        id_clean = re.sub(r'[^a-zA-Z0-9]', '', doc_id).lower()

        # Also check the file number patterns
        # e.g., doc_id "UNK-91-10339" with file number "I-91-546"
        file_no_match = re.search(r'File\s+No\.\s*(?:A-|I-|1-)?([\d\-]+)',
                                   doc_data.get("content", {}).get("full_text", ""))

        if op_clean == id_clean:
            issues.append(f"self-cite: Prior opinion '{op}' matches document ID '{doc_id}'")
        elif file_no_match:
            file_no = file_no_match.group(1)
            file_clean = re.sub(r'[^a-zA-Z0-9]', '', file_no).lower()
            op_clean2 = re.sub(r'[^a-zA-Z0-9]', '', op).lower()
            # Check various forms
            if op_clean2 == file_clean or f"i{file_clean}" == op_clean2 or f"a{file_clean}" == op_clean2:
                issues.append(f"self-cite: Prior opinion '{op}' matches document file number '{file_no}'")

    return issues


def check_conclusion_contamination(conclusion: str) -> list[str]:
    """Check for conclusion section contaminated with non-conclusion content."""
    issues = []
    if not conclusion:
        return issues

    # Check if conclusion has embedded footnote numbers and text
    # Common pattern: conclusion text gets footnote text appended
    if re.search(r'\d/\s+(?:Government|Informal|All\s+statutory)', conclusion):
        issues.append("conclusion: Contains footnote text leak (number/ followed by footnote)")

    return issues


def check_qa_text_quality(doc_data: dict) -> list[str]:
    """Check if qa_text in embedding is usable."""
    issues = []
    qa_text = doc_data.get("embedding", {}).get("qa_text", "")

    if not qa_text:
        issues.append("qa_text: Empty qa_text in embedding section")
        return issues

    # Check if qa_text starts with FPPC letterhead garbage
    if qa_text.startswith("Commissio") or qa_text.startswith("California\nFair"):
        # This is OK for docs without sections - just the full text
        pass

    # Check for CJK garbage in qa_text
    cjk_count = len(re.findall(r'[\u3000-\u9fff\uff00-\uffef]', qa_text))
    if cjk_count > 5:
        issues.append(f"qa_text: Contains {cjk_count} CJK characters (OCR garbage in embedding text)")

    return issues


def run_check(json_path: str, pdf_path: str) -> dict:
    """Run all checks on a single document. Returns dict of findings."""
    result = {
        "json_path": json_path,
        "pdf_path": pdf_path,
        "doc_id": None,
        "year": None,
        "json_exists": False,
        "json_parses": False,
        "pdf_exists": False,
        "pdf_readable": False,
        "pdf_page_count": 0,
        "issues": [],
        "info": [],
    }

    # Check JSON
    json_full = PROJECT_ROOT / json_path
    if not json_full.exists():
        result["issues"].append("JSON file does not exist")
        return result
    result["json_exists"] = True

    try:
        with open(json_full) as f:
            doc_data = json.load(f)
        result["json_parses"] = True
    except json.JSONDecodeError as e:
        result["issues"].append(f"JSON parse error: {e}")
        return result

    result["doc_id"] = doc_data.get("id", "UNKNOWN")
    result["year"] = doc_data.get("year")

    # Check PDF
    pdf_full = PROJECT_ROOT / pdf_path
    if not pdf_full.exists():
        result["issues"].append("PDF file does not exist")
        result["pdf_exists"] = False
    else:
        result["pdf_exists"] = True

    # Extract PDF text
    pdf_text, page_count = extract_pdf_text(pdf_path)
    result["pdf_readable"] = bool(pdf_text)
    result["pdf_page_count"] = page_count

    if not pdf_text:
        result["issues"].append("PDF text extraction returned empty")

    full_text = doc_data.get("content", {}).get("full_text", "")

    # ---- Core checks ----

    # 1. Document type
    result["issues"].extend(check_document_type(doc_data, full_text, pdf_text))

    # 2. Sections checks
    sections = doc_data.get("sections", {})
    for section_name in ["question", "conclusion", "facts", "analysis"]:
        section_text = sections.get(section_name)
        if section_text:
            result["issues"].extend(check_boilerplate_in_section(section_text, section_name))
            result["issues"].extend(check_page_header_contamination(section_text, section_name))
            result["issues"].extend(check_ocr_garbage(section_text, section_name))

    # 3. Conclusion-specific checks
    conclusion = sections.get("conclusion")
    if conclusion:
        result["issues"].extend(check_conclusion_contamination(conclusion))

    # 4. Analysis ending check
    analysis = sections.get("analysis")
    if analysis:
        result["issues"].extend(check_analysis_ending(analysis))

    # 5. Date check
    result["issues"].extend(check_date(doc_data, pdf_text or full_text))

    # 6. Citation checks
    result["issues"].extend(check_citations(doc_data.get("citations", {}), full_text))

    # 7. Self-citation check
    result["issues"].extend(check_self_cite(doc_data))

    # 8. QA text quality
    result["issues"].extend(check_qa_text_quality(doc_data))

    # 9. OCR quality in full text
    result["issues"].extend(check_ocr_garbage(full_text, "full_text"))

    # 10. Check extraction method and quality score
    extraction = doc_data.get("extraction", {})
    quality_score = extraction.get("quality_score", 0)
    method = extraction.get("method", "unknown")
    if quality_score < 0.6:
        result["info"].append(f"Low quality score: {quality_score} (method: {method})")

    # 11. Check if sections were found for a doc that has standard headers
    has_standard = sections.get("has_standard_format", False)
    extraction_method = sections.get("extraction_method", "none")
    if extraction_method == "none":
        # Check if PDF actually has section headers that were missed
        for header in ["QUESTION", "CONCLUSION", "ANALYSIS", "FACTS", "DISCUSSION"]:
            if header in (pdf_text or full_text):
                result["issues"].append(f"sections: PDF contains '{header}' header but extraction_method is 'none' (sections not parsed)")
                break

    # 12. Page count mismatch
    json_page_count = extraction.get("page_count", 0)
    if page_count > 0 and json_page_count > 0 and page_count != json_page_count:
        result["issues"].append(f"page_count: JSON says {json_page_count} pages but PDF has {page_count}")

    # 13. Word count sanity
    word_count = extraction.get("word_count", 0)
    if word_count < 50:
        result["issues"].append(f"word_count: Very low word count ({word_count}) - possible extraction failure")

    # Info about section availability
    has_sections = any(sections.get(s) for s in ["question", "conclusion", "facts", "analysis"])
    result["info"].append(f"Sections found: {'Yes' if has_sections else 'No'} (method: {extraction_method})")
    result["info"].append(f"Document type: {doc_data.get('parsed', {}).get('document_type', 'unknown')}")
    result["info"].append(f"Quality score: {quality_score}, Method: {method}")

    return result


def main():
    print("=" * 80)
    print("FPPC Extraction QA Spot-Check: 1975-1995 Era Documents")
    print("=" * 80)
    print()

    all_results = []
    issue_counts = {}

    for json_path, pdf_path in DOCS:
        result = run_check(json_path, pdf_path)
        all_results.append(result)

        doc_id = result["doc_id"] or json_path
        print(f"--- {doc_id} ({result['year']}) ---")
        print(f"  JSON: {result['json_exists']}, PDF: {result['pdf_exists']}, Pages: {result['pdf_page_count']}")

        for info in result["info"]:
            print(f"  INFO: {info}")

        if result["issues"]:
            for issue in result["issues"]:
                print(f"  ISSUE: {issue}")
                # Count issue types
                issue_type = issue.split(":")[0]
                issue_counts[issue_type] = issue_counts.get(issue_type, 0) + 1
        else:
            print("  No issues found.")
        print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()

    total_docs = len(all_results)
    docs_with_issues = sum(1 for r in all_results if r["issues"])
    docs_without_issues = total_docs - docs_with_issues
    total_issues = sum(len(r["issues"]) for r in all_results)

    print(f"Total documents checked: {total_docs}")
    print(f"Documents with issues: {docs_with_issues}")
    print(f"Documents clean: {docs_without_issues}")
    print(f"Total issues found: {total_issues}")
    print()

    print("Issue counts by type:")
    for issue_type, count in sorted(issue_counts.items(), key=lambda x: -x[1]):
        print(f"  {issue_type}: {count}")
    print()

    print("Per-document issue summary:")
    for r in all_results:
        doc_id = r["doc_id"] or r["json_path"]
        n_issues = len(r["issues"])
        marker = "OK" if n_issues == 0 else f"{n_issues} issue(s)"
        print(f"  {doc_id} ({r['year']}): {marker}")
        if r["issues"]:
            for issue in r["issues"]:
                print(f"    - {issue}")
    print()

    # Detailed per-issue analysis
    print("=" * 80)
    print("DETAILED ISSUE ANALYSIS")
    print("=" * 80)
    print()

    # Group issues by type
    issues_by_type = {}
    for r in all_results:
        for issue in r["issues"]:
            issue_type = issue.split(":")[0]
            if issue_type not in issues_by_type:
                issues_by_type[issue_type] = []
            issues_by_type[issue_type].append((r["doc_id"], issue))

    for issue_type in sorted(issues_by_type.keys()):
        entries = issues_by_type[issue_type]
        print(f"[{issue_type}] ({len(entries)} occurrences)")
        for doc_id, issue in entries:
            print(f"  {doc_id}: {issue}")
        print()


if __name__ == "__main__":
    main()
