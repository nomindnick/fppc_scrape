#!/usr/bin/env python3
"""QA spot-check script for 1996-2005 era extracted JSON vs source PDFs."""

import json
import re
import sys
import os

import fitz  # pymupdf

# Documents to review
DOCS = [
    ("data/extracted/1996/96-352.json", "raw_pdfs/1996/96-352.pdf"),
    ("data/extracted/2003/03-093.json", "raw_pdfs/2003/03-093.pdf"),
    ("data/extracted/1997/97-023.json", "raw_pdfs/1997/97-023.pdf"),
    ("data/extracted/2005/05-144.json", "raw_pdfs/2005/05-144.pdf"),
    ("data/extracted/2001/01-110.json", "raw_pdfs/2001/01-110.pdf"),
    ("data/extracted/1997/97-219.json", "raw_pdfs/1997/97-219.pdf"),
    ("data/extracted/1999/99-295.json", "raw_pdfs/1999/99-295.pdf"),
    ("data/extracted/1998/98-157.json", "raw_pdfs/1998/98-157.pdf"),
    ("data/extracted/1998/98-154.json", "raw_pdfs/1998/98-154.pdf"),
    ("data/extracted/2005/05-250.json", "raw_pdfs/2005/05-250.pdf"),
    ("data/extracted/2005/05-169.json", "raw_pdfs/2005/05-169.pdf"),
    ("data/extracted/2003/03-017.json", "raw_pdfs/2003/03-017.pdf"),
    ("data/extracted/1996/96-064.json", "raw_pdfs/1996/96-064.pdf"),
    ("data/extracted/2005/05-221.json", "raw_pdfs/2005/05-221.pdf"),
    ("data/extracted/2005/05-255.json", "raw_pdfs/2005/05-255.pdf"),
    ("data/extracted/1998/98-251.json", "raw_pdfs/1998/98-251.pdf"),
    ("data/extracted/2001/01-166.json", "raw_pdfs/2001/01-166.pdf"),
    ("data/extracted/2001/01-151.json", "raw_pdfs/2001/01-151.pdf"),
    ("data/extracted/2005/05-074.json", "raw_pdfs/2005/05-074.pdf"),
    ("data/extracted/2005/05-131.json", "raw_pdfs/2005/05-131.pdf"),
]

BASE_DIR = "/home/nick/Projects/fppc_scrape"


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
    """Try to find a date in the PDF text."""
    # Common date patterns
    patterns = [
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\s*,?\s*\d{4}',
        r'\d{1,2}/\d{1,2}/\d{2,4}',
    ]
    for pat in patterns:
        m = re.search(pat, pdf_text)
        if m:
            return m.group(0)
    return None


def check_boilerplate_in_section(text, section_name):
    """Check for various boilerplate contamination."""
    issues = []
    if text is None:
        return issues

    # "If you have questions" / "If you have any other questions" boilerplate
    if re.search(r'If (?:you have|I may be of) (?:any )?(?:other |further )?(?:questions|assistance)', text, re.IGNORECASE):
        issues.append(f"{section_name}: contains 'If you have questions' boilerplate")

    # "Sincerely," signature block
    if re.search(r'Sincerely,?', text):
        issues.append(f"{section_name}: contains 'Sincerely' signature block")

    # "Informal assistance does not provide" footnote
    if re.search(r'Informal assistance does not provide', text, re.IGNORECASE):
        issues.append(f"{section_name}: contains informal assistance footnote leak")

    # "All regulatory references" boilerplate (known R1 issue)
    if re.search(r'All regulatory references', text, re.IGNORECASE):
        issues.append(f"{section_name}: contains 'All regulatory references' boilerplate (R1)")

    # "All references are to the Government Code" boilerplate
    if re.search(r'All references (?:are )?to\s+(?:the )?Government Code', text, re.IGNORECASE):
        issues.append(f"{section_name}: contains 'All references to Government Code' boilerplate")

    # "All references to regulations" boilerplate
    if re.search(r'All references to\s+regulations', text, re.IGNORECASE):
        issues.append(f"{section_name}: contains 'All references to regulations' boilerplate")

    return issues


def check_page_header_contamination(text, doc_id, section_name):
    """Check for page header/footer contamination in sections."""
    issues = []
    if text is None:
        return issues

    # "File No." / "Our File No." headers
    if re.search(r'(?:Our )?File No\.', text):
        issues.append(f"{section_name}: contains 'File No.' page header")

    # "Page 2" / "Page No. 2" etc.
    if re.search(r'Page\s*(?:No\.)?\s*\d+', text):
        issues.append(f"{section_name}: contains 'Page N' header")

    # Phone numbers (likely footer)
    if re.search(r'\(\d{3}\)\s+\d{3}[—-]\d{4}', text):
        issues.append(f"{section_name}: contains phone number (likely footer)")

    # Sacramento address (footer)
    if re.search(r'428 J\s*(?:Street|STREET)', text):
        issues.append(f"{section_name}: contains Sacramento address (footer)")

    # "SACRAMENTO, CALIFORNIA" (footer)
    if re.search(r'SACRAMENTO,?\s*CALIFORNIA', text):
        issues.append(f"{section_name}: contains Sacramento address (footer)")

    return issues


def check_self_citation(doc_id, prior_opinions):
    """Check if document cites itself in prior_opinions."""
    if not prior_opinions:
        return []
    issues = []
    # Normalize doc_id for comparison
    doc_id_normalized = doc_id.replace("-", "").lower()
    for op in prior_opinions:
        op_normalized = op.replace("-", "").replace(" ", "").lower()
        if doc_id_normalized in op_normalized:
            issues.append(f"Self-citation leak: '{op}' matches doc ID '{doc_id}'")
    return issues


def check_citation_validity(citations):
    """Check that citations are in valid ranges."""
    issues = []
    gov_codes = citations.get("government_code", [])
    for gc in gov_codes:
        # Extract numeric part
        m = re.match(r'(\d+)', gc)
        if m:
            num = int(m.group(1))
            # Valid ranges: 81000-92000, 1090-1097, 18000-18999, 82000-92000 etc
            valid = False
            if 81000 <= num <= 92000:
                valid = True
            elif 1090 <= num <= 1097:
                valid = True
            elif 18000 <= num <= 18999:
                valid = True
            elif 85000 <= num <= 91015:
                valid = True
            # Also allow some common ones outside typical ranges
            elif num in (21670,):  # Public Utility Code references sometimes leak in
                pass  # Not a Gov Code issue per se
            if not valid and num > 0:
                # Flag only truly suspicious ones
                if not (1000 <= num <= 99999):
                    issues.append(f"Suspicious citation: Gov Code {gc}")
    return issues


def check_footnote_leak(text, section_name):
    """Check for footnote content leaking into section text."""
    issues = []
    if text is None:
        return issues

    # Common footnote patterns
    footnote_patterns = [
        r'Government Code Sections? 8[i1]000[\s—-]+9101[45]',
        r'Commission regulations appear at\s*[Tt]itle',
        r'California Code of Regulations\.',
        r'Commissionregulations appeff',
    ]
    for pat in footnote_patterns:
        if re.search(pat, text):
            issues.append(f"{section_name}: contains footnote content (Gov Code range / regulations reference)")
            break

    return issues


def check_document_type(data, pdf_text):
    """Verify document_type classification."""
    issues = []
    doc_type = data.get("parsed", {}).get("document_type", "unknown")
    doc_id = data.get("id", "")

    # Check for "I-" prefix indicating informal assistance
    if "I-" in pdf_text[:500] or f"I-{doc_id.replace('-', '')}" in pdf_text[:500].replace("-", ""):
        if doc_type != "informal_advice":
            # Check more carefully
            if re.search(r'File No\.\s*I-', pdf_text[:1000]):
                if doc_type != "informal_advice":
                    issues.append(f"Document has 'I-' file number but classified as '{doc_type}' (should be informal_advice)")

    # Check for "informal assistance" language
    if re.search(r'informal assistance', pdf_text[:2000], re.IGNORECASE):
        if doc_type not in ("informal_advice",):
            issues.append(f"Document mentions 'informal assistance' but classified as '{doc_type}'")

    return issues


def check_date_match(json_date, pdf_text):
    """Check if JSON date matches what's in the PDF."""
    issues = []
    if not json_date:
        issues.append("No date extracted")
        return issues

    pdf_date_str = find_date_in_pdf(pdf_text)
    if not pdf_date_str:
        return issues  # Can't verify

    # Parse json_date (YYYY-MM-DD)
    try:
        parts = json_date.split("-")
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
    except (ValueError, IndexError):
        issues.append(f"Malformed date: {json_date}")
        return issues

    month_names = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December"
    }

    # Check if the month name and year appear in pdf_date_str
    expected_month = month_names.get(month, "")
    if expected_month and expected_month not in pdf_date_str:
        issues.append(f"Date month mismatch: JSON={json_date}, PDF date found='{pdf_date_str}'")
    if str(year) not in pdf_date_str:
        issues.append(f"Date year mismatch: JSON={json_date}, PDF date found='{pdf_date_str}'")

    return issues


def check_analysis_ending(analysis_text):
    """Check if analysis section ends cleanly (no signature, closing)."""
    issues = []
    if not analysis_text:
        return issues

    # Get last 200 chars
    tail = analysis_text[-300:]

    # Check for closing formulas
    closing_patterns = [
        r'If you have (?:any )?(?:other |further )?questions',
        r'If I may be of (?:any )?(?:further )?assistance',
        r'please (?:do not hesitate to )?contact me',
        r'please call (?:me|us)',
        r'Sincerely,?',
    ]
    for pat in closing_patterns:
        if re.search(pat, tail, re.IGNORECASE):
            issues.append(f"analysis: ending contains closing formula: '{pat}'")
            break

    return issues


def analyze_doc(json_path, pdf_path):
    """Analyze a single document."""
    result = {
        "id": None,
        "json_exists": False,
        "pdf_exists": False,
        "json_parses": False,
        "issues": [],
        "sections_found": {"question": False, "conclusion": False, "facts": False, "analysis": False},
    }

    full_json = os.path.join(BASE_DIR, json_path)
    full_pdf = os.path.join(BASE_DIR, pdf_path)

    # Check file existence
    result["json_exists"] = os.path.exists(full_json)
    result["pdf_exists"] = os.path.exists(full_pdf)

    if not result["json_exists"]:
        result["issues"].append("JSON file does not exist")
        return result
    if not result["pdf_exists"]:
        result["issues"].append("PDF file does not exist")

    # Parse JSON
    try:
        with open(full_json) as f:
            data = json.load(f)
        result["json_parses"] = True
    except Exception as e:
        result["issues"].append(f"JSON parse error: {e}")
        return result

    result["id"] = data.get("id", "unknown")

    # Extract PDF text
    pdf_text = ""
    if result["pdf_exists"]:
        pdf_text = extract_pdf_text(full_pdf)
        if pdf_text.startswith("ERROR:"):
            result["issues"].append(f"PDF extraction error: {pdf_text}")
            pdf_text = ""

    # Check sections
    sections = data.get("sections", {})
    for sec in ("question", "conclusion", "facts", "analysis"):
        val = sections.get(sec)
        if val and len(val.strip()) > 0:
            result["sections_found"][sec] = True

    # === CONTENT QUALITY CHECKS ===

    # 1. Document type check
    if pdf_text:
        result["issues"].extend(check_document_type(data, pdf_text))

    # 2. Date check
    json_date = data.get("parsed", {}).get("date")
    if pdf_text:
        result["issues"].extend(check_date_match(json_date, pdf_text))

    # 3. Boilerplate checks for each section
    for sec_name in ("question", "conclusion", "facts", "analysis"):
        sec_text = sections.get(sec_name)
        if sec_text:
            result["issues"].extend(check_boilerplate_in_section(sec_text, sec_name))
            result["issues"].extend(check_page_header_contamination(sec_text, result["id"], sec_name))
            result["issues"].extend(check_footnote_leak(sec_text, sec_name))

    # 4. Analysis ending check
    result["issues"].extend(check_analysis_ending(sections.get("analysis")))

    # 5. Self-citation check
    prior_opinions = data.get("citations", {}).get("prior_opinions", [])
    result["issues"].extend(check_self_citation(result["id"], prior_opinions))

    # 6. Citation validity check
    result["issues"].extend(check_citation_validity(data.get("citations", {})))

    # 7. Check question section for footnote/boilerplate after question text
    q_text = sections.get("question", "")
    if q_text:
        # Check if question has Gov Code footnote appended
        if re.search(r'GovernmentCode|Government Code Sections?\s+8[i1]000', q_text):
            result["issues"].append("question: contains Government Code footnote reference appended to question text")

    # 8. Check conclusion for text that appears to be from intro paragraph
    c_text = sections.get("conclusion", "")
    if c_text:
        if re.search(r'This letter is in response to your request', c_text):
            result["issues"].append("conclusion: contains intro paragraph text leak")
        if re.search(r'we are treating your request as one for informal assistance', c_text):
            result["issues"].append("conclusion: contains informal assistance intro text leak")

    # 9. Check for "Page2" / "Page 2" mid-section (page header contamination)
    for sec_name in ("conclusion", "analysis"):
        sec_text = sections.get(sec_name, "")
        if sec_text and re.search(r'\nPage\s*(?:No\.)?\s*\d+\n', sec_text):
            result["issues"].append(f"{sec_name}: page header appears mid-section")

    # 10. Check if requestor_name looks wrong
    requestor = data.get("parsed", {}).get("requestor_name", "")
    if requestor and len(requestor) <= 2 and requestor not in ("", None):
        result["issues"].append(f"requestor_name appears truncated: '{requestor}'")

    # 11. Check requestor_title — should be requestor's title, not FPPC signer's title
    req_title = data.get("parsed", {}).get("requestor_title")
    if req_title:
        # "General Counsel", "Political Reform Consultant" etc are FPPC titles, not requestor titles
        fppc_titles = ["General Counsel", "Political Reform Consultant", "Counsel", "Technical Assistance"]
        for ft in fppc_titles:
            if ft.lower() in req_title.lower():
                # Check if it matches signer title in PDF
                if pdf_text and re.search(r'Sincerely.*?' + re.escape(ft), pdf_text, re.DOTALL | re.IGNORECASE):
                    result["issues"].append(f"requestor_title '{req_title}' appears to be FPPC signer's title, not requestor's")
                    break

    return result


def main():
    print("=" * 80)
    print("QA SPOT-CHECK: 1996-2005 ERA (20 documents)")
    print("=" * 80)
    print()

    all_results = []
    issue_counts = {}

    for json_path, pdf_path in DOCS:
        result = analyze_doc(json_path, pdf_path)
        all_results.append(result)

        # Print per-doc summary
        doc_id = result["id"] or json_path
        n_issues = len(result["issues"])
        status = "OK" if n_issues == 0 else f"{n_issues} ISSUE(S)"
        secs = result["sections_found"]
        sec_str = " ".join([
            f"Q:{'Y' if secs['question'] else 'N'}",
            f"C:{'Y' if secs['conclusion'] else 'N'}",
            f"F:{'Y' if secs['facts'] else 'N'}",
            f"A:{'Y' if secs['analysis'] else 'N'}",
        ])
        print(f"[{doc_id:>8s}] {sec_str}  {status}")

        if n_issues > 0:
            for issue in result["issues"]:
                print(f"           >>> {issue}")
                # Count issue types
                # Simplify issue to a category
                cat = issue.split(":")[0] if ":" in issue else issue[:40]
                issue_counts[cat] = issue_counts.get(cat, 0) + 1
            print()

    # Summary
    print()
    print("=" * 80)
    print("SUMMARY REPORT")
    print("=" * 80)
    print()

    total = len(all_results)
    json_ok = sum(1 for r in all_results if r["json_parses"])
    pdf_ok = sum(1 for r in all_results if r["pdf_exists"])
    print(f"Total documents checked: {total}")
    print(f"JSON files found & parsed: {json_ok}/{total}")
    print(f"PDF files found: {pdf_ok}/{total}")
    print()

    # Section detection rates
    q_count = sum(1 for r in all_results if r["sections_found"]["question"])
    c_count = sum(1 for r in all_results if r["sections_found"]["conclusion"])
    f_count = sum(1 for r in all_results if r["sections_found"]["facts"])
    a_count = sum(1 for r in all_results if r["sections_found"]["analysis"])
    print("Section Detection Rates:")
    print(f"  Question:   {q_count}/{total} ({100*q_count/total:.0f}%)")
    print(f"  Conclusion: {c_count}/{total} ({100*c_count/total:.0f}%)")
    print(f"  Facts:      {f_count}/{total} ({100*f_count/total:.0f}%)")
    print(f"  Analysis:   {a_count}/{total} ({100*a_count/total:.0f}%)")
    print()

    # Issue summary
    docs_with_issues = sum(1 for r in all_results if len(r["issues"]) > 0)
    docs_clean = total - docs_with_issues
    print(f"Documents with NO issues: {docs_clean}/{total}")
    print(f"Documents with issues:    {docs_with_issues}/{total}")
    print()

    if issue_counts:
        print("Issue Breakdown:")
        # Group and sort
        sorted_issues = sorted(issue_counts.items(), key=lambda x: -x[1])
        for cat, count in sorted_issues:
            print(f"  [{count:2d}] {cat}")
    print()

    # List clean docs
    clean_ids = [r["id"] for r in all_results if len(r["issues"]) == 0]
    if clean_ids:
        print(f"Clean documents: {', '.join(clean_ids)}")

    # List problem docs
    problem_docs = [(r["id"], r["issues"]) for r in all_results if len(r["issues"]) > 0]
    if problem_docs:
        print()
        print("Documents with problems:")
        for doc_id, issues in problem_docs:
            print(f"  {doc_id}: {len(issues)} issue(s)")
            for iss in issues:
                print(f"    - {iss}")

    print()
    print("=" * 80)
    print("DETAILED ISSUE ANALYSIS")
    print("=" * 80)

    # Categorize issues into severity
    critical = []  # Content corruption
    moderate = []  # Boilerplate/header leaks
    minor = []     # Metadata issues

    for r in all_results:
        for iss in r["issues"]:
            entry = (r["id"], iss)
            if any(kw in iss for kw in ["Self-citation", "intro paragraph", "informal assistance intro"]):
                critical.append(entry)
            elif any(kw in iss for kw in ["boilerplate", "footnote", "File No", "Page N", "phone", "footer", "signature", "Sincerely", "closing formula", "page header"]):
                moderate.append(entry)
            else:
                minor.append(entry)

    print()
    print(f"CRITICAL (content corruption): {len(critical)}")
    for doc_id, iss in critical:
        print(f"  [{doc_id}] {iss}")

    print()
    print(f"MODERATE (boilerplate/header contamination): {len(moderate)}")
    for doc_id, iss in moderate:
        print(f"  [{doc_id}] {iss}")

    print()
    print(f"MINOR (metadata/classification): {len(minor)}")
    for doc_id, iss in minor:
        print(f"  [{doc_id}] {iss}")

    print()

    # Overall quality
    total_issues = sum(len(r["issues"]) for r in all_results)
    avg_issues = total_issues / total if total > 0 else 0
    print(f"Total issues found: {total_issues}")
    print(f"Average issues per document: {avg_issues:.1f}")

    if docs_clean / total >= 0.8:
        print("\nOVERALL QUALITY: GOOD (>=80% clean)")
    elif docs_clean / total >= 0.6:
        print("\nOVERALL QUALITY: FAIR (60-80% clean)")
    else:
        print("\nOVERALL QUALITY: NEEDS IMPROVEMENT (<60% clean)")


if __name__ == "__main__":
    main()
