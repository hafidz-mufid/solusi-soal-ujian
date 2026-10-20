"""
Job state persistence and recovery management.
Maintains atomic JSON checkpoints, job metadata, per-question progress, and run logs.
"""

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict, field

from exam_solver.config import JOBS_DIR, mask_key


class QuestionStatus:
    PENDING = "PENDING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    SOLVING = "SOLVING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class JobStatus:
    PENDING = "PENDING"
    OCR_DONE = "OCR_DONE"
    IN_PROGRESS = "IN_PROGRESS"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


@dataclass
class SolutionAttempt:
    attempt_number: int
    solution_latex: str
    sympy_checks: Dict[str, Any]
    timestamp: str
    solver_model: str


@dataclass
class VerificationAttempt:
    attempt_number: int
    status: str  # PASS, FAIL, UNCERTAIN, UNVERIFIED
    findings: str
    required_corrections: List[str]
    verifier_model: str
    timestamp: str
    raw_response: Optional[str] = None


@dataclass
class QuestionRecord:
    q_id: str
    title: str
    raw_text: str
    cleaned_text: str
    page_number: int
    status: str = QuestionStatus.PENDING
    ambiguity_note: Optional[str] = None
    user_clarification: Optional[str] = None
    solution_attempts_count: int = 0
    verification_attempts_count: int = 0
    solutions: List[Dict[str, Any]] = field(default_factory=list)
    verifications: List[Dict[str, Any]] = field(default_factory=list)
    final_solution: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuestionRecord":
        return cls(**data)


class JobManager:
    """Manages persistent job directories, atomic state saves, and resumption."""

    def __init__(self, job_id: Optional[str] = None, source_pdf: Optional[str] = None, base_jobs_dir: Path = JOBS_DIR):
        self.base_jobs_dir = Path(base_jobs_dir)
        self.base_jobs_dir.mkdir(parents=True, exist_ok=True)
        
        if job_id is None:
            now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            pdf_slug = Path(source_pdf).stem[:20] if source_pdf else "job"
            # Clean slug
            pdf_slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in pdf_slug)
            self.job_id = f"{now_str}_{pdf_slug}"
        else:
            self.job_id = job_id
            
        self.job_dir = self.base_jobs_dir / self.job_id
        self.job_dir.mkdir(parents=True, exist_ok=True)
        
        self.pages_dir = self.job_dir / "extracted_pages"
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        
        self.questions_dir = self.job_dir / "questions"
        self.questions_dir.mkdir(parents=True, exist_ok=True)
        
        self.state_file = self.job_dir / "state.json"
        self.run_log_file = self.job_dir / "run_log.json"
        self.raw_ocr_file = self.job_dir / "raw_ocr.txt"
        self.transcription_file = self.job_dir / "transcription.txt"
        self.tex_file = self.job_dir / f"{self.job_id}.tex"
        self.pdf_file = self.job_dir / f"{self.job_id}.pdf"
        self.compile_log_file = self.job_dir / "compile.log"

        if self.state_file.is_file():
            self.load()
        else:
            self.data: Dict[str, Any] = {
                "job_id": self.job_id,
                "source_pdf": str(source_pdf) if source_pdf else None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "status": JobStatus.PENDING,
                "metadata": {},
                "questions": {},
                "api_usage": {
                    "gemini_calls": 0,
                    "openrouter_calls": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
                "errors": [],
            }
            self.save()

    def save(self):
        """Atomically persist state.json using a temporary file and replace."""
        self.data["updated_at"] = datetime.now().isoformat()
        temp_file = self.job_dir / "state.json.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, self.state_file)

    def load(self):
        """Load job state from disk."""
        with open(self.state_file, "r", encoding="utf-8") as f:
            self.data = json.load(f)

    def log_event(self, stage: str, message: str, level: str = "INFO", details: Optional[Dict[str, Any]] = None):
        """Append structured event to run_log.json."""
        event = {
            "timestamp": datetime.now().isoformat(),
            "stage": stage,
            "level": level,
            "message": message,
            "details": details or {},
        }
        
        log_entries = []
        if self.run_log_file.is_file():
            try:
                with open(self.run_log_file, "r", encoding="utf-8") as f:
                    log_entries = json.load(f)
            except Exception:
                log_entries = []
                
        log_entries.append(event)
        temp_log = self.job_dir / "run_log.json.tmp"
        with open(temp_log, "w", encoding="utf-8") as f:
            json.dump(log_entries, f, indent=2, ensure_ascii=False)
        os.replace(temp_log, self.run_log_file)

    def set_question(self, question: QuestionRecord):
        """Store or update a question record in memory and per-question file."""
        self.data["questions"][question.q_id] = question.to_dict()
        q_file = self.questions_dir / f"q_{question.q_id}.json"
        with open(q_file, "w", encoding="utf-8") as f:
            json.dump(question.to_dict(), f, indent=2, ensure_ascii=False)
        self.save()

    def get_question(self, q_id: str) -> Optional[QuestionRecord]:
        """Retrieve a question record by ID."""
        q_data = self.data["questions"].get(q_id)
        if q_data:
            return QuestionRecord.from_dict(q_data)
        return None

    def get_all_questions(self) -> List[QuestionRecord]:
        """Return all question records sorted by numerical/lexical order."""
        def sort_key(q_id: str):
            try:
                return (0, int(q_id))
            except ValueError:
                return (1, q_id)
                
        sorted_ids = sorted(self.data["questions"].keys(), key=sort_key)
        return [QuestionRecord.from_dict(self.data["questions"][qid]) for qid in sorted_ids]

    def get_pending_questions(self) -> List[QuestionRecord]:
        """Return questions requiring work (not yet VERIFIED or COMPLETED)."""
        pending = []
        for q in self.get_all_questions():
            if q.status not in (QuestionStatus.VERIFIED, QuestionStatus.COMPLETED):
                pending.append(q)
        return pending

    def record_api_call(self, service: str, tokens_in: int = 0, tokens_out: int = 0):
        """Record an API call and token metrics."""
        usage = self.data["api_usage"]
        if service.lower() == "gemini":
            usage["gemini_calls"] = usage.get("gemini_calls", 0) + 1
        elif service.lower() == "openrouter":
            usage["openrouter_calls"] = usage.get("openrouter_calls", 0) + 1
        usage["tokens_in"] = usage.get("tokens_in", 0) + tokens_in
        usage["tokens_out"] = usage.get("tokens_out", 0) + tokens_out
        self.save()

    def get_summary(self) -> Dict[str, Any]:
        """Generate final execution summary metrics as required by Section 8."""
        questions = self.get_all_questions()
        detected = len(questions)
        completed = sum(1 for q in questions if q.status in (QuestionStatus.VERIFIED, QuestionStatus.COMPLETED))
        verified = sum(1 for q in questions if q.status == QuestionStatus.VERIFIED)
        unverified_or_failed = sum(1 for q in questions if q.status in (QuestionStatus.UNVERIFIED, QuestionStatus.FAILED))
        needs_review = sum(1 for q in questions if q.status == QuestionStatus.NEEDS_REVIEW or q.ambiguity_note is not None)
        
        pdf_exists = self.pdf_file.is_file() and self.pdf_file.stat().st_size > 0
        tex_exists = self.tex_file.is_file() and self.tex_file.stat().st_size > 0
        
        return {
            "job_id": self.job_id,
            "status": self.data.get("status", JobStatus.PENDING),
            "source_pdf": self.data.get("source_pdf"),
            "detected_questions": detected,
            "completed_questions": completed,
            "verified_questions": verified,
            "unverified_or_failed_questions": unverified_or_failed,
            "questions_requiring_human_confirmation": needs_review,
            "tex_generated": tex_exists,
            "tex_path": str(self.tex_file) if tex_exists else None,
            "pdf_generated": pdf_exists,
            "pdf_path": str(self.pdf_file) if pdf_exists else None,
            "api_usage": self.data.get("api_usage", {}),
            "artifacts": {
                "transcription": str(self.transcription_file) if self.transcription_file.is_file() else None,
                "raw_ocr": str(self.raw_ocr_file) if self.raw_ocr_file.is_file() else None,
                "tex": str(self.tex_file) if tex_exists else None,
                "pdf": str(self.pdf_file) if pdf_exists else None,
                "run_log": str(self.run_log_file) if self.run_log_file.is_file() else None,
            }
        }

    @classmethod
    def list_jobs(cls, base_jobs_dir: Path = JOBS_DIR) -> List[Dict[str, Any]]:
        """List all available jobs in the jobs directory."""
        if not base_jobs_dir.is_dir():
            return []
        jobs = []
        for d in sorted(base_jobs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if d.is_dir() and (d / "state.json").is_file():
                try:
                    with open(d / "state.json", "r", encoding="utf-8") as f:
                        data = json.load(f)
                        jobs.append({
                            "job_id": data.get("job_id", d.name),
                            "status": data.get("status"),
                            "source_pdf": data.get("source_pdf"),
                            "created_at": data.get("created_at"),
                            "updated_at": data.get("updated_at"),
                            "question_count": len(data.get("questions", {})),
                        })
                except Exception:
                    pass
        return jobs
