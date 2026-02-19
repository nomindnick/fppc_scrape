"""
LLM-based section extractor for FPPC advice letters (Phase 3B).

Processes documents where regex-based section parsing failed, using Claude Haiku
to extract/synthesize QUESTION and CONCLUSION sections, detect non-standard
document types, and generate summaries.

Usage:
    # Estimate cost before running
    python -m scraper.llm_extractor --estimate-cost

    # Smoke test with a few documents
    python -m scraper.llm_extractor --process-pending --limit 3

    # Process all flagged documents
    python -m scraper.llm_extractor --process-pending

    # Dry run (shows what would be processed without calling API)
    python -m scraper.llm_extractor --dry-run --limit 5

    # Show LLM extraction statistics
    python -m scraper.llm_extractor --stats
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic

from .config import DATA_DIR, PROJECT_ROOT
from .db import get_connection, get_documents_needing_llm, update_llm_extraction_status
from .schema import FPPCDocument, from_json, to_json
from .section_parser import clean_section_content

# =============================================================================
# Constants
# =============================================================================

EXTRACTED_DIR = DATA_DIR / "extracted"

# Pricing for Claude Haiku 4.5 (per million tokens)
HAIKU_INPUT_COST = 0.80
HAIKU_OUTPUT_COST = 4.00

# System prompt for the LLM
SYSTEM_PROMPT = (
    "You are analyzing California FPPC (Fair Political Practices Commission) "
    "advice letters. Extract structured information and return ONLY valid JSON. "
    "Do not wrap in markdown code fences. Do not include any text outside the JSON object."
)

# Map LLM document_type values to schema DocumentType
DOC_TYPE_MAP = {
    "advice_letter": "advice_letter",
    "incoming_request": "correspondence",
    "withdrawal": "correspondence",
    "declination": "correspondence",
    "correspondence": "correspondence",
    "opinion": "opinion",
    "informal_advice": "informal_advice",
    "other": "other",
}


# =============================================================================
# LLMExtractor Class
# =============================================================================


class LLMExtractor:
    """
    Uses Claude Haiku to extract sections from documents where regex parsing failed.

    Processes documents flagged with needs_llm_extraction=1 in the database,
    updates the JSON files with synthetic sections, and marks them as processed.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        verbose: bool = True,
        dry_run: bool = False,
    ):
        self.model = model
        self.verbose = verbose
        self.dry_run = dry_run

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key and not dry_run:
            print("Error: ANTHROPIC_API_KEY not set. Set it in .env or environment.")
            sys.exit(1)

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None

        self.stats = {
            "processed": 0,
            "success": 0,
            "error": 0,
            "skipped": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    def _log(self, msg: str) -> None:
        """Print message if verbose mode is enabled."""
        if self.verbose:
            print(msg)

    def _truncate_text(self, text: str, max_chars: int = 12000) -> str:
        """Truncate text at a word boundary, preserving completeness."""
        if len(text) <= max_chars:
            return text
        # Find the last space before max_chars
        truncated = text[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > max_chars * 0.8:
            truncated = truncated[:last_space]
        return truncated + "\n[... truncated]"

    def _build_prompt(self, doc: FPPCDocument) -> str:
        """Construct the user prompt for LLM extraction."""
        text = self._truncate_text(doc.content.full_text)

        return f"""Analyze this FPPC document and extract structured information.

Document ID: {doc.id}
Year: {doc.year}
Current document_type: {doc.parsed.document_type}
Parsing notes: {doc.sections.parsing_notes or "none"}

Return a JSON object with these fields:
- "document_type": one of "advice_letter", "incoming_request", "withdrawal", "declination", "correspondence", "other"
- "is_fppc_response": true if this is an FPPC response/advice letter, false if it's an incoming request or other document
- "question": the verbatim QUESTION section text if one exists, or null
- "question_synthetic": a 1-3 sentence summary of the legal question being asked (always provide this)
- "conclusion": the verbatim CONCLUSION/SHORT ANSWER section text if one exists, or null
- "conclusion_synthetic": a 1-3 sentence summary of the FPPC's conclusion/answer (always provide if is_fppc_response is true, null otherwise)
- "summary": a 1-2 sentence summary of the entire document
- "extraction_confidence": 0.0-1.0 confidence in your extraction
- "notes": brief notes about extraction (e.g. "withdrawal letter, no Q/C sections")

DOCUMENT TEXT:
{text}"""

    def _salvage_json(self, text: str) -> dict | None:
        """
        Fallback JSON parser for malformed responses.

        Tries to find a JSON object in the text, handling common issues like
        markdown fences, trailing text, etc.
        """
        # Strip markdown code fences
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
        text = text.strip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find a JSON object with regex
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _call_llm(self, prompt: str) -> tuple[dict, int, int]:
        """
        Call the Claude API with retry logic.

        Returns:
            Tuple of (parsed_response_dict, input_tokens, output_tokens)

        Raises:
            Exception if all retries exhausted
        """
        last_error = None

        for attempt in range(3):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )

                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                raw_text = response.content[0].text

                # Try to parse JSON
                try:
                    result = json.loads(raw_text)
                    return result, input_tokens, output_tokens
                except json.JSONDecodeError:
                    # Fallback: strip fences and retry parse
                    result = self._salvage_json(raw_text)
                    if result:
                        return result, input_tokens, output_tokens

                    # If first attempt, retry the API call
                    if attempt < 2:
                        self._log(f"  JSON parse failed (attempt {attempt + 1}), retrying...")
                        time.sleep(2 ** (attempt + 1))
                        continue

                    raise ValueError(f"Could not parse LLM response as JSON: {raw_text[:200]}")

            except anthropic.RateLimitError:
                wait = 2 ** (attempt + 1)
                self._log(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                last_error = "Rate limit exceeded"
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    wait = 2 ** (attempt + 1)
                    self._log(f"  Server error ({e.status_code}), waiting {wait}s...")
                    time.sleep(wait)
                    last_error = str(e)
                else:
                    raise

        raise Exception(f"All retries exhausted: {last_error}")

    def _update_document(self, doc: FPPCDocument, llm_result: dict) -> FPPCDocument:
        """
        Apply LLM extraction results to the document.

        Only overwrites regex-extracted sections if regex found nothing.
        Always sets synthetic fields and updates embedding content.
        """
        # Update sections — only overwrite if regex found nothing
        # Apply boilerplate cleaning to LLM-returned text
        if not doc.sections.question and llm_result.get("question"):
            doc.sections.question = clean_section_content(llm_result["question"])

        if not doc.sections.conclusion and llm_result.get("conclusion"):
            doc.sections.conclusion = clean_section_content(llm_result["conclusion"])

        # Always set synthetic fields
        doc.sections.question_synthetic = llm_result.get("question_synthetic")
        doc.sections.conclusion_synthetic = llm_result.get("conclusion_synthetic")

        # Update extraction metadata
        doc.sections.extraction_method = "llm"
        doc.sections.extraction_confidence = llm_result.get("extraction_confidence", 0.5)

        # Append LLM notes to parsing_notes
        llm_notes = llm_result.get("notes", "")
        if llm_notes:
            existing = doc.sections.parsing_notes or ""
            doc.sections.parsing_notes = f"{existing}; LLM: {llm_notes}" if existing else f"LLM: {llm_notes}"

        # Update document_type if LLM detected non-standard type
        llm_doc_type = llm_result.get("document_type", "")
        if llm_doc_type and llm_doc_type != "advice_letter":
            mapped = DOC_TYPE_MAP.get(llm_doc_type, doc.parsed.document_type)
            doc.parsed.document_type = mapped

        # Rebuild embedding content
        qa_parts = []
        qa_source = "extracted"

        # Prefer extracted Q/C, fall back to synthetic
        q = doc.sections.question or doc.sections.question_synthetic
        c = doc.sections.conclusion or doc.sections.conclusion_synthetic

        if q:
            qa_parts.append(f"QUESTION: {q}")
        if c:
            qa_parts.append(f"CONCLUSION: {c}")

        if qa_parts:
            doc.embedding.qa_text = "\n\n".join(qa_parts)
            # Determine source
            has_extracted = bool(doc.sections.question or doc.sections.conclusion)
            has_synthetic = bool(doc.sections.question_synthetic or doc.sections.conclusion_synthetic)
            if has_extracted and has_synthetic:
                qa_source = "mixed"
            elif has_synthetic:
                qa_source = "synthetic"
            else:
                qa_source = "extracted"
        else:
            # No Q/C at all — keep first_500_words as qa_text
            doc.embedding.qa_text = doc.embedding.first_500_words
            qa_source = "synthetic"

        doc.embedding.qa_source = qa_source
        doc.embedding.summary = llm_result.get("summary")

        return doc

    def process_document(self, doc_row: dict) -> bool:
        """
        Process a single document: load JSON, call LLM, update JSON + DB.

        Args:
            doc_row: Database row dict

        Returns:
            True if successful, False if failed
        """
        doc_id = doc_row["id"]
        json_path_str = doc_row.get("json_path")

        if not json_path_str:
            self._log(f"  No json_path for doc #{doc_id}, skipping")
            self.stats["skipped"] += 1
            return False

        json_path = PROJECT_ROOT / json_path_str
        if not json_path.exists():
            self._log(f"  JSON file not found: {json_path}, skipping")
            self.stats["skipped"] += 1
            return False

        # Load document
        try:
            json_str = json_path.read_text(encoding="utf-8")
            doc = from_json(json_str)
        except Exception as e:
            self._log(f"  Error loading {json_path.name}: {e}")
            self.stats["error"] += 1
            return False

        self._log(f"Processing {doc.id} ({doc.year})...")

        if self.dry_run:
            prompt = self._build_prompt(doc)
            est_tokens = len(prompt) // 4  # rough estimate
            self._log(f"  [DRY RUN] Would send ~{est_tokens} input tokens")
            self.stats["processed"] += 1
            self.stats["success"] += 1
            return True

        # Call LLM
        try:
            prompt = self._build_prompt(doc)
            llm_result, in_tokens, out_tokens = self._call_llm(prompt)
            self.stats["input_tokens"] += in_tokens
            self.stats["output_tokens"] += out_tokens
        except Exception as e:
            self._log(f"  LLM error for {doc.id}: {e}")
            self.stats["error"] += 1
            return False

        # Update document
        doc = self._update_document(doc, llm_result)

        # Save updated JSON
        try:
            json_content = to_json(doc)
            json_path.write_text(json_content, encoding="utf-8")
        except Exception as e:
            self._log(f"  Error saving {json_path.name}: {e}")
            self.stats["error"] += 1
            return False

        # Update database
        try:
            update_llm_extraction_status(
                doc_id=doc_id,
                section_confidence=doc.sections.extraction_confidence,
            )
        except Exception as e:
            self._log(f"  DB update error for {doc.id}: {e}")
            self.stats["error"] += 1
            return False

        self._log(f"  Done: confidence={doc.sections.extraction_confidence:.2f}, "
                   f"type={doc.parsed.document_type}, tokens={in_tokens}+{out_tokens}")
        self.stats["processed"] += 1
        self.stats["success"] += 1
        return True

    def process_all(self, limit: int | None = None) -> dict:
        """
        Process all documents flagged for LLM extraction.

        Args:
            limit: Optional limit on number of documents

        Returns:
            Statistics dict
        """
        self.stats = {
            "processed": 0,
            "success": 0,
            "error": 0,
            "skipped": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

        docs = get_documents_needing_llm(limit=limit)
        total = len(docs)
        self._log(f"Found {total} documents needing LLM extraction")

        if total == 0:
            self._log("Nothing to process.")
            return self.stats

        for i, doc_row in enumerate(docs, 1):
            self.process_document(doc_row)

            # Progress update every 10 documents
            if i % 10 == 0 and i < total:
                cost = self._estimate_cost_from_tokens()
                self._log(f"Progress: {i}/{total} "
                           f"({self.stats['success']} ok, {self.stats['error']} err, "
                           f"~${cost:.2f} so far)")

        return self.stats

    def _estimate_cost_from_tokens(self) -> float:
        """Estimate cost from actual token usage."""
        input_cost = (self.stats["input_tokens"] / 1_000_000) * HAIKU_INPUT_COST
        output_cost = (self.stats["output_tokens"] / 1_000_000) * HAIKU_OUTPUT_COST
        return input_cost + output_cost

    def estimate_cost(self) -> dict:
        """
        Estimate cost for processing all pending documents.

        Reads all flagged JSON files, estimates token counts from text length.
        """
        docs = get_documents_needing_llm()
        total_chars = 0
        doc_count = 0
        missing = 0

        for doc_row in docs:
            json_path_str = doc_row.get("json_path")
            if not json_path_str:
                missing += 1
                continue

            json_path = PROJECT_ROOT / json_path_str
            if not json_path.exists():
                missing += 1
                continue

            try:
                json_str = json_path.read_text(encoding="utf-8")
                doc = from_json(json_str)
                # Estimate: truncated text + prompt overhead
                text_len = min(len(doc.content.full_text), 12000)
                total_chars += text_len + 500  # 500 chars for prompt template
                doc_count += 1
            except Exception:
                missing += 1

        # Rough token estimate: 1 token ≈ 4 chars
        est_input_tokens = total_chars // 4
        est_output_tokens = doc_count * 300  # ~300 output tokens per doc

        input_cost = (est_input_tokens / 1_000_000) * HAIKU_INPUT_COST
        output_cost = (est_output_tokens / 1_000_000) * HAIKU_OUTPUT_COST
        total_cost = input_cost + output_cost

        return {
            "documents": doc_count,
            "missing_json": missing,
            "est_input_tokens": est_input_tokens,
            "est_output_tokens": est_output_tokens,
            "est_input_cost": input_cost,
            "est_output_cost": output_cost,
            "est_total_cost": total_cost,
        }


# =============================================================================
# Statistics
# =============================================================================


def get_llm_stats() -> dict:
    """Get LLM extraction statistics from the database."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    cursor.execute("SELECT COUNT(*) FROM documents WHERE needs_llm_extraction = 1")
    stats["flagged"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM documents WHERE llm_extracted_at IS NOT NULL")
    stats["completed"] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM documents
        WHERE needs_llm_extraction = 1
        AND llm_extracted_at IS NULL
    """)
    stats["pending"] = cursor.fetchone()[0]

    # By year
    cursor.execute("""
        SELECT year_tag, COUNT(*) as count
        FROM documents
        WHERE needs_llm_extraction = 1
        AND llm_extracted_at IS NULL
        GROUP BY year_tag
        ORDER BY year_tag DESC
    """)
    stats["pending_by_year"] = {row["year_tag"]: row["count"] for row in cursor.fetchall()}

    conn.close()
    return stats


def print_stats() -> None:
    """Print LLM extraction statistics."""
    stats = get_llm_stats()

    print("\n" + "=" * 60)
    print("LLM EXTRACTION STATISTICS (Phase 3B)")
    print("=" * 60)

    print(f"\n  Flagged for LLM:  {stats['flagged']:,}")
    print(f"  Completed:        {stats['completed']:,}")
    print(f"  Pending:          {stats['pending']:,}")

    if stats.get("pending_by_year"):
        print(f"\n  Pending by year:")
        for year, count in list(stats["pending_by_year"].items())[:10]:
            print(f"    {year}: {count}")

    print()


# =============================================================================
# CLI
# =============================================================================


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="LLM-based section extraction for FPPC documents (Phase 3B)"
    )
    parser.add_argument(
        "--process-pending",
        action="store_true",
        help="Process all documents flagged for LLM extraction",
    )
    parser.add_argument(
        "--estimate-cost",
        action="store_true",
        help="Estimate API cost for pending documents",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without calling API",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of documents to process",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show LLM extraction statistics",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model to use (default: claude-haiku-4-5-20251001)",
    )

    args = parser.parse_args()

    if args.stats:
        print_stats()
        return

    if args.estimate_cost:
        extractor = LLMExtractor(model=args.model, dry_run=True)
        estimate = extractor.estimate_cost()
        print("\n" + "=" * 60)
        print("COST ESTIMATE (Phase 3B LLM Extraction)")
        print("=" * 60)
        print(f"\n  Documents to process: {estimate['documents']:,}")
        print(f"  Missing JSON files:   {estimate['missing_json']}")
        print(f"\n  Est. input tokens:    {estimate['est_input_tokens']:,}")
        print(f"  Est. output tokens:   {estimate['est_output_tokens']:,}")
        print(f"\n  Est. input cost:      ${estimate['est_input_cost']:.2f}")
        print(f"  Est. output cost:     ${estimate['est_output_cost']:.2f}")
        print(f"  Est. total cost:      ${estimate['est_total_cost']:.2f}")
        print(f"\n  Model: {args.model}")
        print()
        return

    if args.process_pending or args.dry_run:
        extractor = LLMExtractor(
            model=args.model,
            verbose=True,
            dry_run=args.dry_run,
        )
        stats = extractor.process_all(limit=args.limit)

        cost = extractor._estimate_cost_from_tokens()
        print("\n" + "=" * 60)
        print("LLM EXTRACTION COMPLETE" if not args.dry_run else "DRY RUN COMPLETE")
        print("=" * 60)
        print(f"  Processed: {stats['processed']}")
        print(f"  Success:   {stats['success']}")
        print(f"  Errors:    {stats['error']}")
        print(f"  Skipped:   {stats['skipped']}")
        if not args.dry_run:
            print(f"  Tokens:    {stats['input_tokens']:,} in / {stats['output_tokens']:,} out")
            print(f"  Cost:      ~${cost:.2f}")
        print()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
