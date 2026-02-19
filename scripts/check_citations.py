#!/usr/bin/env python3
import fitz, re

checks = [
    ('07-025', 2007, ['A-02-080', 'A-03-179', 'A-99-153', 'A-00-174']),
    ('06-024', 2006, ['A-94-247']),
    ('13-092', 2013, ['I-98-324']),
    ('08-003', 2008, ['A-99-032']),
    ('06-197', 2006, ['A-86-044']),
    ('09-129A', 2009, ['A-95-333']),
    ('09-261', 2009, ['A-09-216']),
    ('09-079', 2009, ['A-07-031']),
    ('09-148', 2009, ['A-07-158']),
]

for letter_id, year, priors in checks:
    pdf_path = f'/home/nick/Projects/fppc_scrape/raw_pdfs/{year}/{letter_id}.pdf'
    doc = fitz.open(pdf_path)
    text = ''.join([page.get_text() for page in doc])
    doc.close()
    print(f'\n--- {letter_id} ({year}) ---')
    for p in priors:
        num_part = p.split('-', 1)[1] if '-' in p else p
        patterns = [p, num_part, p.replace('A-', '4-'), p.replace('I-', '1-')]
        found = False
        for pat in patterns:
            if pat in text:
                idx = text.index(pat)
                ctx = text[max(0,idx-50):idx+len(pat)+50]
                print(f'  {p}: FOUND as "{pat}" -> ...{repr(ctx[:80])}...')
                found = True
                break
        if not found:
            # Try without dash
            nodash = num_part.replace('-', '')
            if nodash in text:
                idx = text.index(nodash)
                ctx = text[max(0,idx-30):idx+len(nodash)+30]
                print(f'  {p}: FOUND variant "{nodash}" -> {repr(ctx[:60])}')
            else:
                print(f'  {p}: *** NOT FOUND in PDF ***')
