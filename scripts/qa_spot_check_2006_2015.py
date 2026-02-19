#!/usr/bin/env python3
"""
QA spot-check script for 20 documents from the 2006-2015 era.
Reads JSON + PDF for each doc, checks for extraction quality issues.
"""

import json
import re
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import fitz  # pymupdf
except ImportError:
    print("ERROR: pymupdf not installed. Run: pip install pymupdf")
    sys.exit(1)

PROJECT_ROOT = "/home/nick/Projects/fppc_scrape"

DOCS = [
    {"id": "15-202",      "json": "data/extracted/2015/15-202.json",      "pdf": "raw_pdfs/2015/15-202.pdf"},
    {"id": "08-134",      "json": "data/extracted/2008/08-134.json",      "pdf": "raw_pdfs/2008/08-134.pdf"},
    {"id": "08-016",      "json": "data/extracted/2008/08-016.json",      "pdf": "raw_pdfs/2008/08-016.pdf"},
    {"id": "08-139",      "json": "data/extracted/2008/08-139.json",      "pdf": "raw_pdfs/2008/08-139.pdf"},
    {"id": "11-110",      "json": "data/extracted/2011/11-110.json",      "pdf": "raw_pdfs/2011/11-110.pdf"},
    {"id": "14-038",      "json": "data/extracted/2014/14-038.json",      "pdf": "raw_pdfs/2014/14-038.pdf"},
    {"id": "14-021",      "json": "data/extracted/2014/14-021.json",      "pdf": "raw_pdfs/2014/14-021.pdf"},
    {"id": "06-025",      "json": "data/extracted/2006/06-025.json",      "pdf": "raw_pdfs/2006/06-025.pdf"},
    {"id": "15-057",      "json": "data/extracted/2015/15-057.json",      "pdf": "raw_pdfs/2015/15-057.pdf"},
    {"id": "06-066",      "json": "data/extracted/2006/06-066.json",      "pdf": "raw_pdfs/2006/06-066.pdf"},
    {"id": "14-025W",     "json": "data/extracted/2014/14-025W.json",     "pdf": "raw_pdfs/2014/14-025W.pdf"},
    {"id": "06-075",      "json": "data/extracted/2006/06-075.json",      "pdf": "raw_pdfs/2006/06-075.pdf"},
    {"id": "07-158",      "json": "data/extracted/2007/07-158.json",      "pdf": "raw_pdfs/2007/07-158.pdf"},
    {"id": "07-122",      "json": "data/extracted/2007/07-122.json",      "pdf": "raw_pdfs/2007/07-122.pdf"},
    {"id": "12-105",      "json": "data/extracted/2012/12-105.json",      "pdf": "raw_pdfs/2012/12-105.pdf"},
    {"id": "09-031",      "json": "data/extracted/2009/09-031.json",      "pdf": "raw_pdfs/2009/09-031.pdf"},
    {"id": "08-195",      "json": "data/extracted/2008/08-195.json",      "pdf": "raw_pdfs/2008/08-195.pdf"},
    {"id": "11-062",      "json": "data/extracted/2011/11-062.json",      "pdf": "raw_pdfs/2011/11-062.pdf"},
    {"id": "11-119",      "json": "data/extracted/2011/11-119.json",      "pdf": "raw_pdfs/2011/11-119.pdf"},
    {"id": "15-213-1090", "json": "data/extracted/2015/15-213-1090.json", "pdf": "raw_pdfs/2015/15-213-1090.pdf"},
]

# Closing boilerplate patterns that should NOT appear in section text
BOILERPLATE_PATTERNS = [
    r"If you have (?:other )?questions",
    r"Should you have (?:other |any )?questions",
    r"If you have other questions on this matter",
    r"please (?:do not hesitate to )?contact me at",
    r"Sincerely",
    r"General Counsel",
    r"Counsel, Legal Division",
    r"Senior Counsel",
    r"^[A-Z]{1,4}:[a-z]{1,4}$",  # initials like MFC:jgl
]

# Page header patterns
PAGE_HEADER_PATTERNS = [
    r"File No\. [A-Z]?-?\d{2}-\d{3}",
    r"Page No\. \d+",
    r"Page \d+ of \d+",
]

# Self-citation pattern (doc citing itself)
SELF_CITE_PATTERN = r"A-{id}"

def extract_pdf_text(pdf_path):
    """Extract text from PDF using pymupdf."""
    full_path = os.path.join(PROJECT_ROOT, pdf_path)
    if not os.path.exists(full_path):
        return None, f"PDF not found: {full_path}"
    try:
        doc = fitz.open(full_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text, None
    except Exception as e:
        return None, f"PDF extraction error: {e}"

def find_date_in_pdf(pdf_text):
    """Try to find a date in the PDF text."""
    # Common date patterns in FPPC letters
    month_names = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    pattern = rf"({month_names}\s+\d{{1,2}},?\s+\d{{4}})"
    matches = re.findall(pattern, pdf_text)
    return matches

def check_section_in_pdf(section_text, pdf_text, section_name):
    """Check if section text roughly matches what's in the PDF."""
    if not section_text or not pdf_text:
        return None
    # Take first 80 chars of section, normalize whitespace
    snippet = re.sub(r'\s+', ' ', section_text[:80]).strip()
    pdf_norm = re.sub(r'\s+', ' ', pdf_text)
    if snippet[:40] in pdf_norm:
        return True
    return False

def check_boilerplate(text, doc_id):
    """Check if closing boilerplate appears in section text."""
    issues = []
    if not text:
        return issues
    for pattern in BOILERPLATE_PATTERNS:
        matches = re.findall(pattern, text, re.MULTILINE)
        if matches:
            # Find the location - show last 100 chars around the match
            for m in re.finditer(pattern, text, re.MULTILINE):
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 40)
                context = text[start:end].replace('\n', ' ')
                issues.append(f"Boilerplate match '{pattern}': ...{context}...")
    return issues

def check_page_headers(text, doc_id):
    """Check if page headers contaminate section text."""
    issues = []
    if not text:
        return issues
    for pattern in PAGE_HEADER_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            issues.append(f"Page header: {matches[0]}")
    return issues

def check_self_citation(citations, doc_id):
    """Check if document cites itself in prior_opinions."""
    issues = []
    if not citations:
        return issues
    prior = citations.get("prior_opinions", [])
    # Normalize doc_id: 15-202 -> check for A-15-202
    for p in prior:
        p_norm = p.replace("A-", "").replace("I-", "").replace("W-", "")
        if p_norm == doc_id or p_norm == doc_id.replace("-", ""):
            issues.append(f"Self-citation: {p} matches doc {doc_id}")
    return issues

def check_footnote_leak(text):
    """Check if footnotes leak into section text improperly."""
    issues = []
    if not text:
        return issues
    # Footnote markers at weird places
    # Pattern: number at start of line that looks like a footnote
    fn_pattern = r'(?:^|\n)\s*\d+\s+(?:The |See |Under |Section |Government )'
    matches = re.findall(fn_pattern, text)
    if matches:
        for m in matches:
            issues.append(f"Possible footnote leak: {m.strip()[:60]}")
    return issues

def check_document_type(data, pdf_text):
    """Check if document_type is correctly classified."""
    doc_id = data["id"]
    doc_type = data.get("parsed", {}).get("document_type", "unknown")
    issues = []

    # 'W' suffix means withdrawal
    if doc_id.endswith("W") and doc_type != "correspondence":
        issues.append(f"Withdrawal doc '{doc_id}' classified as '{doc_type}' instead of 'correspondence'")

    # '1090' suffix means Section 1090 letter
    if "1090" in doc_id:
        # These are valid advice letters about Section 1090
        pass

    # Check if it's actually a withdrawal letter
    if pdf_text and "withdrawn" in pdf_text.lower() and "request for advice" in pdf_text.lower():
        if "withdrawn your request" in pdf_text.lower() or "we have withdrawn" in pdf_text.lower():
            if doc_type != "correspondence":
                issues.append(f"Withdrawal content but classified as '{doc_type}'")

    return issues

def analyze_doc(doc_info):
    """Full analysis of one document."""
    doc_id = doc_info["id"]
    json_path = os.path.join(PROJECT_ROOT, doc_info["json"])
    pdf_path = doc_info["pdf"]

    result = {
        "id": doc_id,
        "issues": [],
        "warnings": [],
        "sections_found": {"question": False, "conclusion": False, "facts": False, "analysis": False},
        "is_standard": True,  # standard advice letter (not correspondence/withdrawal)
    }

    # 1. Read JSON
    if not os.path.exists(json_path):
        result["issues"].append("JSON file not found")
        return result

    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        result["issues"].append(f"JSON parse error: {e}")
        return result

    # 2. Extract PDF text
    pdf_text, pdf_err = extract_pdf_text(pdf_path)
    if pdf_err:
        result["issues"].append(pdf_err)

    # 3. Check document type
    doc_type = data.get("parsed", {}).get("document_type", "unknown")
    if doc_type in ("correspondence", "withdrawal"):
        result["is_standard"] = False

    type_issues = check_document_type(data, pdf_text or "")
    result["issues"].extend(type_issues)

    # 4. Check sections
    sections = data.get("sections", {})
    for sec_name in ["question", "conclusion", "facts", "analysis"]:
        sec_text = sections.get(sec_name)
        if sec_text:
            result["sections_found"][sec_name] = True

            # Check section content matches PDF
            if pdf_text:
                match = check_section_in_pdf(sec_text, pdf_text, sec_name)
                if match is False:
                    result["warnings"].append(f"Section '{sec_name}' start not found in PDF text")

            # Check for boilerplate contamination
            bp_issues = check_boilerplate(sec_text, doc_id)
            for bp in bp_issues:
                result["issues"].append(f"[{sec_name}] {bp}")

            # Check for page header contamination
            ph_issues = check_page_headers(sec_text, doc_id)
            for ph in ph_issues:
                result["issues"].append(f"[{sec_name}] {ph}")

            # Check for footnote leaks
            fn_issues = check_footnote_leak(sec_text)
            for fn in fn_issues:
                result["warnings"].append(f"[{sec_name}] {fn}")

            # Check section ending - last 200 chars
            ending = sec_text[-200:] if len(sec_text) > 200 else sec_text
            ending_bp = check_boilerplate(ending, doc_id)
            if ending_bp:
                for bp in ending_bp:
                    result["issues"].append(f"[{sec_name} ENDING] {bp}")
        else:
            if result["is_standard"]:
                # Missing section is only an issue for standard advice letters
                pass  # We'll track via sections_found

    # 5. Check date
    parsed_date = data.get("parsed", {}).get("date")
    parsed_date_raw = data.get("parsed", {}).get("date_raw")
    if pdf_text:
        pdf_dates = find_date_in_pdf(pdf_text)
        if parsed_date_raw and pdf_dates:
            # Check if our parsed date appears in the PDF dates
            found = False
            for pd in pdf_dates:
                pd_norm = re.sub(r'\s+', ' ', pd).strip()
                pdr_norm = re.sub(r'\s+', ' ', parsed_date_raw).strip() if parsed_date_raw else ""
                if pdr_norm and pdr_norm in pd_norm:
                    found = True
                    break
            if not found:
                result["warnings"].append(f"Date '{parsed_date_raw}' not confirmed in PDF dates: {pdf_dates[:3]}")
        elif not parsed_date and result["is_standard"]:
            result["issues"].append("No date extracted")

    # 6. Check citations
    citations = data.get("citations", {})
    self_cite_issues = check_self_citation(citations, doc_id)
    result["issues"].extend(self_cite_issues)

    # Check if Gov Code 1090 is cited as a prior opinion (it's a code section, not an opinion)
    prior_ops = citations.get("prior_opinions", [])
    for p in prior_ops:
        if re.match(r'^1090$', p):
            result["issues"].append(f"Gov Code '1090' in prior_opinions instead of government_code")

    # 7. Check for "All regulatory references" boilerplate in sections
    for sec_name in ["question", "conclusion", "facts", "analysis"]:
        sec_text = sections.get(sec_name, "") or ""
        if "All regulatory references are to" in sec_text:
            result["issues"].append(f"[{sec_name}] Boilerplate: 'All regulatory references are to...'")
        if "All statutory references are to" in sec_text:
            # This is often part of a legitimate introductory footnote, check context
            if "The Political Reform Act is contained in" in sec_text:
                result["issues"].append(f"[{sec_name}] Intro boilerplate leaked: 'The Political Reform Act is contained in...'")
        if "does not act as a finder of fact" in sec_text:
            result["issues"].append(f"[{sec_name}] Intro boilerplate leaked: 'does not act as a finder of fact'")

    # 8. Full-text specific checks
    full_text = data.get("content", {}).get("full_text", "")

    # Check if section headers exist in PDF for standard docs
    if result["is_standard"] and pdf_text:
        for header in ["QUESTION", "CONCLUSION", "FACTS", "ANALYSIS"]:
            if header in pdf_text and not sections.get(header.lower()):
                result["issues"].append(f"'{header}' header found in PDF but section not extracted")

    # 9. Check analysis ending for signature block contamination
    analysis_text = sections.get("analysis", "") or ""
    if analysis_text:
        last_200 = analysis_text[-200:]
        sig_patterns = [
            r"Sincerely",
            r"General Counsel",
            r"Legal Division",
            r"^By:$",
            r"[A-Z][a-z]+ [A-Z]\. [A-Z][a-z]+",  # Name patterns like "Hyla P. Wagner"
        ]
        for pat in sig_patterns:
            if re.search(pat, last_200, re.MULTILINE):
                result["issues"].append(f"[analysis ENDING] Possible signature block: matches '{pat}'")
                break

    # 10. Check conclusion ending
    conclusion_text = sections.get("conclusion", "") or ""
    if conclusion_text:
        last_100 = conclusion_text[-100:]
        # Conclusion should end with substantive content, not boilerplate
        if re.search(r"If you have|Should you have|please contact", last_100, re.IGNORECASE):
            result["issues"].append("[conclusion ENDING] Boilerplate at end of conclusion")

    return result

def main():
    print("=" * 80)
    print("QA SPOT-CHECK: 2006-2015 ERA (20 documents)")
    print("=" * 80)
    print()

    all_results = []
    issue_counts = {}

    # Count standard docs for section detection rates
    standard_docs = 0
    section_counts = {"question": 0, "conclusion": 0, "facts": 0, "analysis": 0}

    for doc in DOCS:
        result = analyze_doc(doc)
        all_results.append(result)

        if result["is_standard"]:
            standard_docs += 1
            for sec in ["question", "conclusion", "facts", "analysis"]:
                if result["sections_found"][sec]:
                    section_counts[sec] += 1

        # Count issues by type
        for issue in result["issues"]:
            # Categorize
            if "Boilerplate" in issue or "boilerplate" in issue:
                cat = "boilerplate_contamination"
            elif "Page header" in issue:
                cat = "page_header_contamination"
            elif "Self-citation" in issue:
                cat = "self_citation_leak"
            elif "footnote" in issue.lower():
                cat = "footnote_leak"
            elif "signature" in issue.lower():
                cat = "signature_block_leak"
            elif "not found" in issue.lower() and "PDF" in issue:
                cat = "missing_file"
            elif "document_type" in issue.lower() or "classified" in issue.lower() or "Withdrawal" in issue:
                cat = "misclassification"
            elif "header found in PDF" in issue:
                cat = "missed_section"
            elif "date" in issue.lower():
                cat = "date_issue"
            else:
                cat = "other"
            issue_counts[cat] = issue_counts.get(cat, 0) + 1

    # ===== DETAILED RESULTS PER DOCUMENT =====
    print("-" * 80)
    print("DETAILED RESULTS PER DOCUMENT")
    print("-" * 80)

    for result in all_results:
        doc_id = result["id"]
        is_std = "STANDARD" if result["is_standard"] else "NON-STANDARD"
        secs = result["sections_found"]
        sec_str = f"Q={'Y' if secs['question'] else 'N'} C={'Y' if secs['conclusion'] else 'N'} F={'Y' if secs['facts'] else 'N'} A={'Y' if secs['analysis'] else 'N'}"

        n_issues = len(result["issues"])
        n_warnings = len(result["warnings"])

        status = "PASS" if n_issues == 0 else f"FAIL ({n_issues} issues)"

        print(f"\n  [{doc_id}] ({is_std}) Sections: {sec_str} -- {status}")

        if result["issues"]:
            for issue in result["issues"]:
                print(f"    ISSUE: {issue}")
        if result["warnings"]:
            for warn in result["warnings"]:
                print(f"    WARN:  {warn}")

    # ===== SUMMARY =====
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    print(f"\nTotal documents: {len(all_results)}")
    print(f"Standard advice letters: {standard_docs}")
    print(f"Non-standard (correspondence/withdrawal): {len(all_results) - standard_docs}")

    print(f"\n--- Section Detection Rates (standard docs only, N={standard_docs}) ---")
    for sec in ["question", "conclusion", "facts", "analysis"]:
        count = section_counts[sec]
        pct = (count / standard_docs * 100) if standard_docs > 0 else 0
        print(f"  {sec:12s}: {count}/{standard_docs} ({pct:.0f}%)")

    print(f"\n--- Issue Counts by Type ---")
    total_issues = 0
    for cat, count in sorted(issue_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:30s}: {count}")
        total_issues += count
    print(f"  {'TOTAL':30s}: {total_issues}")

    # Docs with issues
    problem_docs = [r for r in all_results if r["issues"]]
    clean_docs = [r for r in all_results if not r["issues"]]

    print(f"\n--- Document Status ---")
    print(f"  Clean (no issues): {len(clean_docs)}/{len(all_results)}")
    print(f"  With issues:       {len(problem_docs)}/{len(all_results)}")

    if problem_docs:
        print(f"\n--- Problematic Documents ---")
        for r in problem_docs:
            print(f"  {r['id']}: {len(r['issues'])} issue(s)")
            for issue in r['issues']:
                print(f"      - {issue}")

    # Overall quality
    print(f"\n--- Overall Quality Assessment ---")
    std_clean = len([r for r in all_results if r["is_standard"] and not r["issues"]])
    std_total = standard_docs
    if std_total > 0:
        quality_pct = std_clean / std_total * 100
        print(f"  Standard docs clean: {std_clean}/{std_total} ({quality_pct:.0f}%)")

    all_sections = sum(section_counts.values())
    max_sections = standard_docs * 4
    if max_sections > 0:
        sec_pct = all_sections / max_sections * 100
        print(f"  Overall section detection: {all_sections}/{max_sections} ({sec_pct:.0f}%)")

    print()

if __name__ == "__main__":
    main()
