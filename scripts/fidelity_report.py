#!/usr/bin/env python3
"""
Generate fidelity verification report for the FPPC corpus.

Reads the database fidelity columns and all phase reports to produce a
comprehensive summary of corpus fidelity status.

Usage:
    python scripts/fidelity_report.py                # Generate report
    python scripts/fidelity_report.py --json          # Output JSON instead of markdown
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.config import DATA_DIR
from scraper.db import get_connection

REPORT_DIR = DATA_DIR / "qa_reports"
CANARY_REPORT = REPORT_DIR / "canary_scan.json"
HIGH_RISK_REPORT = REPORT_DIR / "high_risk_verification.json"
MEDIUM_RISK_REPORT = REPORT_DIR / "medium_risk_sampling.json"
OUTPUT_PATH = REPORT_DIR / "fidelity_report.md"


def get_db_stats() -> dict:
    """Pull fidelity statistics from the database."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    # Total extracted
    cursor.execute("SELECT COUNT(*) FROM documents WHERE extraction_status = 'extracted'")
    stats["total_extracted"] = cursor.fetchone()[0]

    # By fidelity risk tier
    cursor.execute("""
        SELECT fidelity_risk, COUNT(*) as count
        FROM documents
        WHERE extraction_status = 'extracted'
        GROUP BY fidelity_risk
        ORDER BY count DESC
    """)
    stats["by_risk"] = {row["fidelity_risk"] or "unassessed": row["count"]
                        for row in cursor.fetchall()}

    # By fidelity method
    cursor.execute("""
        SELECT fidelity_method, COUNT(*) as count
        FROM documents
        WHERE extraction_status = 'extracted'
        GROUP BY fidelity_method
        ORDER BY count DESC
    """)
    stats["by_method"] = {row["fidelity_method"] or "unassessed": row["count"]
                          for row in cursor.fetchall()}

    # By extraction method + fidelity risk
    cursor.execute("""
        SELECT extraction_method, fidelity_risk, COUNT(*) as count
        FROM documents
        WHERE extraction_status = 'extracted'
        GROUP BY extraction_method, fidelity_risk
        ORDER BY extraction_method, fidelity_risk
    """)
    stats["by_method_risk"] = [(row["extraction_method"], row["fidelity_risk"] or "unassessed",
                                row["count"]) for row in cursor.fetchall()]

    # Fidelity score distribution for olmOCR docs
    cursor.execute("""
        SELECT
            CASE
                WHEN fidelity_score IS NULL THEN 'unscored'
                WHEN fidelity_score >= 0.90 THEN '0.90+'
                WHEN fidelity_score >= 0.80 THEN '0.80-0.90'
                WHEN fidelity_score >= 0.70 THEN '0.70-0.80'
                WHEN fidelity_score >= 0.50 THEN '0.50-0.70'
                ELSE '< 0.50'
            END as band,
            COUNT(*) as count
        FROM documents
        WHERE extraction_status = 'extracted'
        AND extraction_method = 'olmocr'
        GROUP BY band
    """)
    stats["olmocr_fidelity_dist"] = {row["band"]: row["count"]
                                      for row in cursor.fetchall()}

    # Average fidelity score by extraction method
    cursor.execute("""
        SELECT extraction_method,
               ROUND(AVG(fidelity_score), 4) as avg_score,
               COUNT(*) as count
        FROM documents
        WHERE extraction_status = 'extracted'
        AND fidelity_score IS NOT NULL
        GROUP BY extraction_method
    """)
    stats["avg_fidelity"] = [(row["extraction_method"], row["avg_score"], row["count"])
                              for row in cursor.fetchall()]

    # Count still needing assessment
    cursor.execute("""
        SELECT COUNT(*) FROM documents
        WHERE extraction_status = 'extracted'
        AND fidelity_score IS NULL
    """)
    stats["unassessed"] = cursor.fetchone()[0]

    # Worst docs (lowest fidelity scores)
    cursor.execute("""
        SELECT id, letter_id, year_tag, extraction_method,
               fidelity_score, fidelity_method, fidelity_risk
        FROM documents
        WHERE extraction_status = 'extracted'
        AND fidelity_score IS NOT NULL
        AND fidelity_risk IN ('critical', 'high')
        ORDER BY fidelity_score ASC
        LIMIT 20
    """)
    stats["worst_docs"] = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return stats


def load_phase_reports() -> dict:
    """Load data from phase report files."""
    reports = {}

    if CANARY_REPORT.exists():
        with open(CANARY_REPORT) as f:
            data = json.load(f)
        reports["canary"] = {
            "total_scanned": data.get("total_scanned", 0),
            "tier_distribution": data.get("tier_distribution", {}),
            "description_mode_count": data.get("description_mode_count", 0),
            "elapsed": data.get("elapsed_seconds", 0),
        }

    if HIGH_RISK_REPORT.exists():
        with open(HIGH_RISK_REPORT) as f:
            data = json.load(f)
        reports["high_risk"] = {
            "total_verified": data.get("total_verified", 0),
            "verified_ok": data.get("verified_ok", 0),
            "hallucinated": data.get("hallucinated", 0),
            "fixed": data.get("fixed", 0),
            "cost": data.get("total_cost", 0),
        }

    if MEDIUM_RISK_REPORT.exists():
        with open(MEDIUM_RISK_REPORT) as f:
            data = json.load(f)
        reports["medium"] = {
            "sample_size": data.get("sample_size", 0),
            "total_medium": data.get("medium_tier_total", 0),
            "error_rate": data.get("error_rate", 0),
            "decision": data.get("decision", "unknown"),
            "cost": data.get("total_cost", 0),
        }

    return reports


def generate_markdown(db_stats: dict, reports: dict) -> str:
    """Generate the fidelity report as markdown."""
    lines = []
    lines.append("# Corpus Fidelity Verification Report")
    lines.append(f"\nGenerated: {datetime.now():%Y-%m-%d %H:%M}")
    lines.append("")

    # Executive Summary
    total = db_stats["total_extracted"]
    by_risk = db_stats["by_risk"]
    verified = by_risk.get("verified", 0) + by_risk.get("low", 0)
    at_risk = by_risk.get("critical", 0) + by_risk.get("high", 0)
    unassessed = db_stats["unassessed"]

    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"| Metric | Count | % |")
    lines.append(f"|--------|------:|--:|")
    lines.append(f"| Total extracted | {total:,} | 100% |")
    lines.append(f"| Verified/trusted | {verified:,} | {verified/total*100:.1f}% |")
    lines.append(f"| Medium risk | {by_risk.get('medium', 0):,} | {by_risk.get('medium', 0)/total*100:.1f}% |")
    lines.append(f"| High/critical risk | {at_risk:,} | {at_risk/total*100:.1f}% |")
    lines.append(f"| Unassessed | {unassessed:,} | {unassessed/total*100:.1f}% |")
    lines.append("")

    # Fidelity by Risk Tier
    lines.append("## Risk Tier Distribution")
    lines.append("")
    lines.append("| Risk Tier | Count | Description |")
    lines.append("|-----------|------:|-------------|")
    tier_desc = {
        "verified": "Confirmed faithful (native or Haiku-verified)",
        "low": "Canary score > 0.70 — minor divergence, trusted",
        "medium": "Canary score 0.50-0.70 — some divergence",
        "high": "Canary score < 0.50 — significant divergence",
        "critical": "Description-mode or canary < 0.30",
        "unassessed": "Not yet evaluated",
    }
    for tier in ["verified", "low", "medium", "high", "critical", "unassessed"]:
        count = by_risk.get(tier, 0)
        if count > 0:
            lines.append(f"| {tier} | {count:,} | {tier_desc.get(tier, '')} |")
    lines.append("")

    # Fidelity by Assessment Method
    lines.append("## Assessment Methods Used")
    lines.append("")
    lines.append("| Method | Count | Description |")
    lines.append("|--------|------:|-------------|")
    method_desc = {
        "native_trusted": "PyMuPDF native extraction (deterministic, faithful)",
        "tesseract_canary": "Compared against Tesseract baseline",
        "haiku_verified": "Verified by Claude Haiku vision",
        "haiku_verified_tesseract": "Hallucination detected, replaced with Tesseract",
        "haiku_unreadable": "Image too blurry for Haiku to read",
        "olmocr_retry": "Re-extracted via olmOCR (description-mode fixed)",
        "tesseract_fallback": "Replaced with Tesseract extraction",
        "unassessed": "Not yet evaluated",
    }
    for method, count in sorted(db_stats["by_method"].items(), key=lambda x: -x[1]):
        lines.append(f"| {method} | {count:,} | {method_desc.get(method, '')} |")
    lines.append("")

    # olmOCR Fidelity Score Distribution
    if db_stats["olmocr_fidelity_dist"]:
        lines.append("## olmOCR Fidelity Score Distribution")
        lines.append("")
        lines.append("| Score Band | Count |")
        lines.append("|-----------|------:|")
        for band in ["0.90+", "0.80-0.90", "0.70-0.80", "0.50-0.70", "< 0.50", "unscored"]:
            count = db_stats["olmocr_fidelity_dist"].get(band, 0)
            if count > 0:
                lines.append(f"| {band} | {count:,} |")
        lines.append("")

    # Phase Reports
    if reports:
        lines.append("## Phase Outcomes")
        lines.append("")

        if "canary" in reports:
            c = reports["canary"]
            lines.append("### Phase 1: Tesseract Canary Scan")
            lines.append(f"- Scanned: {c['total_scanned']} olmOCR documents")
            lines.append(f"- Duration: {c['elapsed'] / 3600:.1f} hours")
            lines.append(f"- Description-mode detected: {c['description_mode_count']}")
            if c["tier_distribution"]:
                lines.append(f"- Tiers: " + ", ".join(
                    f"{t}={n}" for t, n in c["tier_distribution"].items()
                ))
            lines.append("")

        if "high_risk" in reports:
            h = reports["high_risk"]
            lines.append("### Phase 4: High-Risk Haiku Verification")
            lines.append(f"- Verified: {h['total_verified']} documents")
            lines.append(f"- Verified OK: {h['verified_ok']}")
            lines.append(f"- Hallucinated: {h['hallucinated']}")
            lines.append(f"- Fixed: {h['fixed']}")
            lines.append(f"- Cost: ${h['cost']:.2f}")
            lines.append("")

        if "medium" in reports:
            m = reports["medium"]
            lines.append("### Phase 5: Medium-Risk Sampling")
            lines.append(f"- Sampled: {m['sample_size']} of {m['total_medium']} medium-risk docs")
            lines.append(f"- Error rate: {m['error_rate'] * 100:.1f}%")
            lines.append(f"- Decision: **{m['decision']}**")
            lines.append(f"- Cost: ${m['cost']:.2f}")
            lines.append("")

    # Worst Documents
    if db_stats["worst_docs"]:
        lines.append("## Worst Documents (Critical/High Risk)")
        lines.append("")
        lines.append("| ID | Letter ID | Year | Method | Fidelity | Risk |")
        lines.append("|----|-----------|------|--------|----------|------|")
        for d in db_stats["worst_docs"][:20]:
            lines.append(
                f"| {d['id']} | {d['letter_id'] or '?'} | {d['year_tag']} "
                f"| {d['extraction_method']} | {d['fidelity_score']:.3f} "
                f"| {d['fidelity_risk']} |"
            )
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate fidelity verification report")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of markdown")
    args = parser.parse_args()

    db_stats = get_db_stats()
    reports = load_phase_reports()

    if args.json:
        output = {
            "generated": datetime.now().isoformat(),
            "database": db_stats,
            "phase_reports": reports,
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        md = generate_markdown(db_stats, reports)
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            f.write(md)
        print(md)
        print(f"\nReport saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
