"""
Question parser, mathematical preservation, and ambiguity detection engine.
Splits transcribed text into ordered questions and detects ambiguities requiring review.
"""

import re
from typing import List, Dict, Any, Tuple, Optional
from exam_solver.state import QuestionRecord, QuestionStatus, JobManager


# Regex patterns matching start of question blocks
QUESTION_START_PATTERNS = [
    # E.g. "Soal 1", "Problem 1", "Question 1"
    re.compile(r"^(?:(?:Soal|Problem|Question|Nomor|No\.?)\s*(\d+))(?:\s*[:.)-]|\s+|$)", re.IGNORECASE),
    # E.g. "[CO 1]" or "[CO1]" (Course Outcome from user's template)
    re.compile(r"^\[CO\s*(\d+)\]", re.IGNORECASE),
    # E.g. "1. " or "1) " at the start of a line
    re.compile(r"^(\d+)[\.\)]\s+(?=[A-Z\$\*\[])"),
]

# Patterns indicating potential material ambiguities
AMBIGUITY_PATTERNS = [
    (re.compile(r"\?{2,}"), "Tanda tanya berulang (teks/angka tidak jelas atau hilang)"),
    (re.compile(r"\[(?:unreadable|tidak terbaca|hilang|\.\.\.)\]", re.IGNORECASE), "Bagian soal ditandai tidak terbaca"),
    (re.compile(r"_{3,}"), "Garis kosong/isian yang belum terisi nilai (missing parameter)"),
    (re.compile(r"(?:perhatikan|lihat|tinjau)\s+(?:gambar|grafik|diagram|kurva|sketsa)\s+(?:di|pada)?\s*(?:bawah|samping|atas|berikut)?", re.IGNORECASE), "Soal merujuk pada gambar/grafik/diagram"),
    (re.compile(r"(?:figure|fig\.|diagram|sketch)\s*(?:\d+)?", re.IGNORECASE), "Question references an external figure or diagram"),
    (re.compile(r"[=+\-*/]\s*[=+\-*/]{2,}"), "Simbol matematika rusak/anomali operator"),
]


def detect_question_header(line: str) -> Optional[Tuple[str, str]]:
    """
    Check if a line begins a new question.
    Returns (question_id, title) or None.
    """
    stripped = line.strip()
    if not stripped:
        return None
        
    for pat in QUESTION_START_PATTERNS:
        m = pat.match(stripped)
        if m:
            q_num = m.group(1)
            return (q_num, f"Soal {q_num}")
            
    return None


def detect_ambiguities(text: str) -> List[str]:
    """Scan text for ambiguities that could materially affect solutions."""
    notes = []
    for pat, desc in AMBIGUITY_PATTERNS:
        if pat.search(text):
            notes.append(desc)
    return notes


def parse_questions_from_pages(pages_content: List[Dict[str, Any]]) -> List[QuestionRecord]:
    """
    Parse questions across all pages, preserving order, sub-items, and math formulas.
    """
    extracted_questions = []
    current_q_id = None
    current_title = None
    current_lines = []
    current_page = 1
    
    # Track preamble text before Question 1 if any (e.g. instructions)
    for p in pages_content:
        page_num = p["page_number"]
        page_text = p.get("text", "")
        lines = page_text.splitlines()
        
        for line in lines:
            header_info = detect_question_header(line)
            if header_info:
                # Save previous question if exists
                if current_q_id is not None and current_lines:
                    full_text = "\n".join(current_lines).strip()
                    ambiguities = detect_ambiguities(full_text)
                    amb_note = "; ".join(ambiguities) if ambiguities else None
                    status = QuestionStatus.NEEDS_REVIEW if ambiguities else QuestionStatus.PENDING
                    
                    extracted_questions.append(QuestionRecord(
                        q_id=current_q_id,
                        title=current_title,
                        raw_text=full_text,
                        cleaned_text=full_text,
                        page_number=current_page,
                        status=status,
                        ambiguity_note=amb_note
                    ))
                    
                current_q_id, current_title = header_info
                current_page = page_num
                current_lines = [line]
            else:
                if current_q_id is not None:
                    current_lines.append(line)
                    
    # Append the final question
    if current_q_id is not None and current_lines:
        full_text = "\n".join(current_lines).strip()
        ambiguities = detect_ambiguities(full_text)
        amb_note = "; ".join(ambiguities) if ambiguities else None
        status = QuestionStatus.NEEDS_REVIEW if ambiguities else QuestionStatus.PENDING
        
        extracted_questions.append(QuestionRecord(
            q_id=current_q_id,
            title=current_title,
            raw_text=full_text,
            cleaned_text=full_text,
            page_number=current_page,
            status=status,
            ambiguity_note=amb_note
        ))

    # If no headers were matched (e.g., single-question exam or non-standard format),
    # treat each page or the whole document as Question 1
    if not extracted_questions and pages_content:
        all_text = "\n\n".join(p.get("text", "") for p in pages_content).strip()
        if all_text:
            ambiguities = detect_ambiguities(all_text)
            amb_note = "; ".join(ambiguities) if ambiguities else None
            status = QuestionStatus.NEEDS_REVIEW if ambiguities else QuestionStatus.PENDING
            extracted_questions.append(QuestionRecord(
                q_id="1",
                title="Soal 1",
                raw_text=all_text,
                cleaned_text=all_text,
                page_number=1,
                status=status,
                ambiguity_note=amb_note
            ))
            
    return extracted_questions


def generate_transcription_file(job_mgr: JobManager, questions: List[QuestionRecord]) -> Path:
    """
    Generate and save the full transcription.txt file with standardized delimiters.
    """
    lines = [
        "=" * 80,
        f"TRANSKRIPSI SOAL UJIAN - JOB: {job_mgr.job_id}",
        f"Sumber: {job_mgr.data.get('source_pdf')}",
        f"Jumlah Soal Terdeteksi: {len(questions)}",
        "=" * 80,
        "",
    ]
    
    for q in questions:
        lines.append("=" * 80)
        lines.append(f"{q.title.upper()} (Halaman {q.page_number})")
        if q.ambiguity_note:
            lines.append(f"[PERHATIAN / AMBIGUITAS]: {q.ambiguity_note}")
        lines.append("=" * 80)
        lines.append(q.cleaned_text)
        lines.append("")
        lines.append("-" * 80)
        lines.append("")
        
    with open(job_mgr.transcription_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        
    job_mgr.log_event("TRANSCRIPTION", f"Generated {job_mgr.transcription_file.name} with {len(questions)} questions.")
    return job_mgr.transcription_file
