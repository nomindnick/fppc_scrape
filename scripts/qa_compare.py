#!/usr/bin/env python3
"""Compare extracted JSON against source PDFs for QA review."""
import fitz
import json
import os
import re
import sys

base = '/home/nick/Projects/fppc_scrape'

def check_doc(letter_id, year, doc_id):
    pdf_path = f'{base}/raw_pdfs/{year}/{letter_id}.pdf'
    json_path = f'{base}/data/extracted/{year}/{letter_id}.json'

    print(f'\n{"="*80}')
    print(f'DOC: {letter_id} ({year}) [doc_id={doc_id}]')

    if not os.path.exists(pdf_path):
        print('  PDF NOT FOUND')
        return

    doc = fitz.open(pdf_path)
    pc = doc.page_count
    pdf_text = ''.join([page.get_text() for page in doc])
    doc.close()
    pdf_words = len(pdf_text.split())

    if not os.path.exists(json_path):
        print('  JSON NOT FOUND')
        return

    with open(json_path) as f:
        data = json.load(f)

    content = data.get('content', {})
    full_text = content.get('full_text', '')
    ft_words = len(full_text.split()) if full_text else 0
    extraction = data.get('extraction', {})
    parsed = data.get('parsed', {})
    sections = data.get('sections', {})
    citations = data.get('citations', {})
    embed = data.get('embedding', {})
    classification = data.get('classification', {})

    # Date check
    date_parsed = parsed.get('date')
    date_raw = parsed.get('date_raw', '')
    date_patterns = [r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}']
    pdf_dates = re.findall(date_patterns[0], pdf_text)

    # Section check in PDF
    has_q_pdf = bool(re.search(r'\bQUESTION', pdf_text))
    has_c_pdf = bool(re.search(r'\bCONCLUSION', pdf_text))
    has_f_pdf = bool(re.search(r'\bFACTS', pdf_text))
    has_a_pdf = bool(re.search(r'\bANALYSIS', pdf_text))

    q = sections.get('question', '') or ''
    c = sections.get('conclusion', '') or ''
    f_sec = sections.get('facts', '') or ''
    a = sections.get('analysis', '') or ''

    # Citation check
    pdf_gov_codes = sorted(set(re.findall(r'[Ss]ection[s]?\s+(\d{4,5})', pdf_text)))
    pdf_regs_raw = sorted(set(re.findall(r'(?:[Rr]egulation|[Ss]ection)\s+(\d{5}(?:\.\d+)?)', pdf_text)))
    pdf_priors = sorted(set(re.findall(r'(?:No\.\s*|Letter[,]?\s*(?:No\.\s*)?)([AI]?-?\d{2}-\d{2,3})', pdf_text)))

    json_gov_codes = sorted(citations.get('government_code', []))
    json_regs = sorted(citations.get('regulations', []))
    json_priors = sorted(citations.get('prior_opinions', []))

    # Boilerplate check
    bp_phrases = [
        'All regulatory references',
        'The Political Reform Act is contained in Government Code',
        'Commission regulations appear at Title 2',
        'The political reform act is contained',
    ]
    boilerplate_in_sections = []
    for bp in bp_phrases:
        for sname, sval in [('question', q), ('conclusion', c), ('facts', f_sec), ('analysis', a)]:
            if bp.lower() in sval.lower():
                boilerplate_in_sections.append(f'{sname}: "{bp[:40]}..."')

    # qa_text check
    qa = embed.get('qa_text', '')
    qa_has_bp = any(bp.lower() in qa.lower() for bp in bp_phrases)

    # Print
    print(f'  PDF_PATH: {pdf_path}')
    print(f'  TEXT_QUALITY: PDF={pdf_words}w, JSON={ft_words}w, ratio={ft_words/max(pdf_words,1):.2f}')

    # Check for garbled text
    garbled_chars = len(re.findall(r'[^\x00-\x7F\u2018\u2019\u201C\u201D\u2013\u2014\u2026\u00A0-\u00FF]', full_text or pdf_text))
    total_chars = len(full_text or pdf_text)
    garbled_ratio = garbled_chars / max(total_chars, 1)
    if garbled_ratio > 0.02:
        print(f'  TEXT: GARBLED ({garbled_ratio:.1%} non-ASCII)')
    elif garbled_ratio > 0.005:
        print(f'  TEXT: MINOR_OCR_ARTIFACTS ({garbled_ratio:.1%} non-ASCII)')
    else:
        print(f'  TEXT: OK')

    # Date assessment
    if pdf_dates and date_parsed:
        print(f'  DATE: parsed={date_parsed}, raw="{date_raw}", pdf_dates={pdf_dates[:2]}')
    elif not date_parsed and pdf_dates:
        print(f'  DATE: MISSING - pdf has dates: {pdf_dates[:2]}')
    elif date_parsed and not pdf_dates:
        print(f'  DATE: parsed={date_parsed} (no standard date found in PDF text)')
    else:
        print(f'  DATE: MISSING (no date in PDF either)')

    # Section assessment
    pdf_sections = {'Q': has_q_pdf, 'C': has_c_pdf, 'F': has_f_pdf, 'A': has_a_pdf}
    json_sections = {'Q': bool(q), 'C': bool(c), 'F': bool(f_sec), 'A': bool(a)}
    missing = [k for k in pdf_sections if pdf_sections[k] and not json_sections[k]]
    extra = [k for k in json_sections if json_sections[k] and not pdf_sections[k]]

    if missing:
        print(f'  SECTIONS: MISSING_EXPECTED - PDF has {missing} but JSON does not')
    elif pdf_sections == json_sections:
        print(f'  SECTIONS: OK (PDF={pdf_sections}, JSON={json_sections})')
    else:
        print(f'  SECTIONS: MISMATCH (PDF={pdf_sections}, JSON={json_sections})')

    print(f'  SECTION_CONF: {sections.get("extraction_confidence")} method={sections.get("extraction_method")}')

    # Section boundary checks
    if q:
        q_trunc = len(q) < 20
        print(f'  Q: {len(q)}c starts="{q[:80]}..."' + (' TRUNCATED' if q_trunc else ''))
    if c:
        print(f'  C: {len(c)}c starts="{c[:80]}..."')
    if f_sec:
        print(f'  F: {len(f_sec)}c starts="{f_sec[:80]}..."')
    if a:
        print(f'  A: {len(a)}c starts="{a[:80]}..."')
        print(f'     ends="{a[-80:]}..."')

    # Citations
    print(f'  GOV_CODES: json={json_gov_codes}')
    print(f'  REGS: json={json_regs}')
    print(f'  PRIORS: json={json_priors}')

    # Check for fabricated citations (in JSON but not findable in full text)
    for gc in json_gov_codes:
        if gc not in (full_text or '') and gc not in pdf_text:
            print(f'  FABRICATED_CITATION: gov_code {gc} not in text')
    for r in json_regs:
        if r not in (full_text or '') and r not in pdf_text:
            print(f'  FABRICATED_CITATION: regulation {r} not in text')
    for p in json_priors:
        if p not in (full_text or '') and p not in pdf_text:
            print(f'  FABRICATED_CITATION: prior_opinion {p} not in text')

    # Boilerplate
    if boilerplate_in_sections:
        print(f'  BOILERPLATE: CONTAMINATED - {boilerplate_in_sections}')
    else:
        print(f'  BOILERPLATE: CLEAN')

    # qa_text
    print(f'  QA_TEXT: {len(qa)}c, has_boilerplate={qa_has_bp}, source={embed.get("qa_source")}')

    # Confidence calibration
    conf = sections.get('extraction_confidence', 0)
    has_all = all([q, c, f_sec, a])
    has_none = not any([q, c, f_sec, a])
    if conf >= 0.9 and not has_all:
        print(f'  CONFIDENCE: TOO_HIGH ({conf} but missing sections)')
    elif conf <= 0.2 and has_all:
        print(f'  CONFIDENCE: TOO_LOW ({conf} but has all sections)')
    else:
        print(f'  CONFIDENCE: OK ({conf})')

    # full_text match
    if full_text and pdf_text:
        ft_norm = re.sub(r'\s+', ' ', full_text[:100]).strip()
        pdf_norm = re.sub(r'\s+', ' ', pdf_text[:100]).strip()
        if ft_norm[:50] != pdf_norm[:50]:
            print(f'  FULLTEXT_MATCH: DIFFERS from PDF')
            print(f'    PDF: {repr(pdf_norm[:60])}')
            print(f'    JSON: {repr(ft_norm[:60])}')
        else:
            print(f'  FULLTEXT_MATCH: OK')
    elif not full_text and pdf_words > 0:
        print(f'  FULLTEXT_MATCH: EMPTY (PDF has {pdf_words} words)')

docs = [
    ("06-069", 2006, 13730),
    ("07-085", 2007, 1767),
    ("09-101", 2009, 2074),
    ("12-108W", 2012, 2634),
    ("11-100", 2011, 2497),
    ("13-106", 2013, 2769),
    ("07-025", 2007, 13744),
    ("06-024", 2006, 1635),
    ("08-012", 2008, 1856),
    ("13-092", 2013, 13944),
    ("08-002", 2008, 1868),
    ("13-043", 2013, 2836),
    ("06-197", 2006, 1683),
    ("10-039", 2010, 2304),
    ("11-144", 2011, 2514),
    ("07-123", 2007, 1843),
    ("08-003", 2008, 1869),
    ("09-129A", 2009, 2067),
    ("15-022", 2015, 3077),
    ("09-261", 2009, 2233),
    ("11-068", 2011, 2479),
    ("09-079", 2009, 2115),
    ("12-096", 2012, 2666),
    ("09-148", 2009, 2197),
    ("13-136", 2013, 2782),
]

for letter_id, year, doc_id in docs:
    check_doc(letter_id, year, doc_id)
