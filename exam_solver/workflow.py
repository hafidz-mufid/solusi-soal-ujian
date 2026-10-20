"""
End-to-End Exam Solving Workflow Orchestrator.
Coordinates OCR, question extraction, Gemini solving, OpenRouter verification,
LaTeX assembly, Tectonic PDF compilation, and atomic state checkpointing.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from exam_solver.config import config, MAX_SOLVER_ATTEMPTS
from exam_solver.state import JobManager, JobStatus, QuestionRecord, QuestionStatus, SolutionAttempt, VerificationAttempt
from exam_solver.ocr import extract_pdf_content
from exam_solver.parser import parse_questions_from_pages, generate_transcription_file
from exam_solver.math_checks import check_solution_consistency
from exam_solver.solver import GeminiSolver
from exam_solver.verifier import OpenRouterVerifier
from exam_solver.latex_engine import LatexEngine


class ExamWorkflow:
    """Orchestrates end-to-end exam solving with resumption and strict retry bounds."""

    def __init__(
        self,
        job_mgr: JobManager,
        solver: Optional[GeminiSolver] = None,
        verifier: Optional[OpenRouterVerifier] = None,
        latex_engine: Optional[LatexEngine] = None,
    ):
        self.job_mgr = job_mgr
        self.solver = solver or GeminiSolver()
        self.verifier = verifier or OpenRouterVerifier()
        self.latex_engine = latex_engine or LatexEngine()

    def run_ocr_and_transcription(self, dpi: int = 200) -> List[QuestionRecord]:
        """Stage A & B: Process PDF, run local OCR, split questions, generate transcription.txt."""
        # Check if already processed
        existing_questions = self.job_mgr.get_all_questions()
        if existing_questions and self.job_mgr.transcription_file.is_file():
            self.job_mgr.log_event("WORKFLOW", f"Using existing transcription with {len(existing_questions)} questions.")
            return existing_questions

        self.job_mgr.log_event("WORKFLOW", "Starting Stage A & B: OCR and Transcription.")
        pages_content = extract_pdf_content(self.job_mgr, dpi=dpi)
        questions = parse_questions_from_pages(pages_content)

        for q in questions:
            self.job_mgr.set_question(q)

        generate_transcription_file(self.job_mgr, questions)
        self.job_mgr.data["status"] = JobStatus.OCR_DONE
        self.job_mgr.save()
        return questions

    def solve_and_verify_question(
        self,
        q_id: str,
        user_clarification: Optional[str] = None,
    ) -> QuestionRecord:
        """
        Stage C & D: Solve a single question independently with Gemini,
        run deterministic checks, verify with OpenRouter, and perform at most
        one revision if verification fails.
        """
        q = self.job_mgr.get_question(q_id)
        if not q:
            raise ValueError(f"Question {q_id} not found in job {self.job_mgr.job_id}")

        # Check if already completed/verified
        if q.status in (QuestionStatus.VERIFIED, QuestionStatus.COMPLETED):
            self.job_mgr.log_event("WORKFLOW", f"Question {q_id} is already {q.status}. Skipping reprocessing.")
            return q

        if user_clarification:
            q.user_clarification = user_clarification
            if q.status == QuestionStatus.NEEDS_REVIEW:
                q.status = QuestionStatus.PENDING

        page_img = self.job_mgr.pages_dir / f"page_{q.page_number}.png"
        img_to_pass = page_img if page_img.is_file() else None

        # Loop for maximum MAX_SOLVER_ATTEMPTS (2 attempts)
        while q.solution_attempts_count < MAX_SOLVER_ATTEMPTS:
            attempt_num = q.solution_attempts_count + 1
            q.status = QuestionStatus.SOLVING
            self.job_mgr.set_question(q)

            self.job_mgr.log_event("WORKFLOW", f"Question {q_id}: Attempt {attempt_num}/{MAX_SOLVER_ATTEMPTS} starting.")

            # Prepare solver inputs
            prev_sol = q.solutions[-1]["solution_latex"] if q.solutions else None
            prev_critique = q.verifications[-1]["findings"] if q.verifications else None

            try:
                solver_res = self.solver.solve_question(
                    question_text=q.cleaned_text,
                    image_path=img_to_pass,
                    previous_solution=prev_sol,
                    verifier_feedback=prev_critique,
                    user_clarification=q.user_clarification,
                    job_mgr=self.job_mgr,
                )
            except Exception as solve_err:
                err_str = str(solve_err)
                q.error = err_str
                q.status = QuestionStatus.FAILED
                self.job_mgr.set_question(q)
                self.job_mgr.log_event("WORKFLOW", f"Question {q_id}: Solver failed: {err_str}", level="ERROR")
                break

            solution_latex = solver_res["solution_latex"]
            
            # Local deterministic checks
            math_check_report = check_solution_consistency(q.cleaned_text, solution_latex)
            
            # Record solution attempt
            q.solution_attempts_count = attempt_num
            q.solutions.append({
                "attempt_number": attempt_num,
                "solution_latex": solution_latex,
                "sympy_checks": math_check_report,
                "timestamp": solver_res.get("elapsed_seconds"),
                "solver_model": solver_res.get("model"),
            })
            q.final_solution = solution_latex
            self.job_mgr.set_question(q)

            # Independent verification with OpenRouter
            q.status = QuestionStatus.VERIFYING
            self.job_mgr.set_question(q)

            v_res = self.verifier.verify_solution(
                question_text=q.cleaned_text,
                candidate_solution=solution_latex,
                job_mgr=self.job_mgr,
            )

            v_status = v_res.get("status", "UNVERIFIED")
            q.verification_attempts_count += 1
            q.verifications.append({
                "attempt_number": attempt_num,
                "status": v_status,
                "findings": v_res.get("findings", ""),
                "required_corrections": v_res.get("required_corrections", []),
                "verifier_model": v_res.get("model"),
                "timestamp": v_res.get("elapsed_seconds"),
            })

            self.job_mgr.log_event("WORKFLOW", f"Question {q_id}: Verification Attempt {attempt_num} status = {v_status}.")

            if v_status == "PASS":
                q.status = QuestionStatus.VERIFIED
                self.job_mgr.set_question(q)
                break
            elif v_status == "FAIL":
                if q.solution_attempts_count >= MAX_SOLVER_ATTEMPTS:
                    q.status = QuestionStatus.FAILED
                    self.job_mgr.set_question(q)
                    self.job_mgr.log_event("WORKFLOW", f"Question {q_id}: Max attempts ({MAX_SOLVER_ATTEMPTS}) reached with failed verification.", level="WARNING")
                    break
                else:
                    self.job_mgr.log_event("WORKFLOW", f"Question {q_id}: Verification failed on attempt {attempt_num}. Proceeding to revision attempt.")
            else:
                # UNCERTAIN or UNVERIFIED (e.g. verifier unavailable or key missing)
                q.status = QuestionStatus.UNVERIFIED
                self.job_mgr.set_question(q)
                break

        self.job_mgr.set_question(q)
        return q

    def solve_all_questions(
        self,
        interactive: bool = False,
        prompt_clarification_fn: Optional[Callable[[QuestionRecord], Optional[str]]] = None,
    ) -> List[QuestionRecord]:
        """Solve and verify all pending questions in the job."""
        self.job_mgr.data["status"] = JobStatus.IN_PROGRESS
        self.job_mgr.save()

        all_questions = self.job_mgr.get_all_questions()
        for q in all_questions:
            # Skip if already verified or completed
            if q.status in (QuestionStatus.VERIFIED, QuestionStatus.COMPLETED):
                continue

            # Check ambiguity
            clarification = None
            if q.status == QuestionStatus.NEEDS_REVIEW and interactive and prompt_clarification_fn:
                clarification = prompt_clarification_fn(q)

            self.solve_and_verify_question(q.q_id, user_clarification=clarification)

        return self.job_mgr.get_all_questions()

    def assemble_latex_and_compile(
        self,
        exam_metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Stage E & F: Generate full LaTeX document and compile to PDF."""
        self.job_mgr.log_event("WORKFLOW", "Starting Stage E & F: LaTeX assembly and PDF compilation.")
        questions = self.job_mgr.get_all_questions()
        
        tex_content = self.latex_engine.generate_latex(questions, exam_metadata=exam_metadata)
        with open(self.job_mgr.tex_file, "w", encoding="utf-8") as f:
            f.write(tex_content)
            
        self.job_mgr.log_event("WORKFLOW", f"Generated LaTeX file: {self.job_mgr.tex_file.name}")

        compile_result = self.latex_engine.compile(
            self.job_mgr.tex_file,
            output_dir=self.job_mgr.job_dir,
            job_mgr=self.job_mgr,
        )

        if compile_result["success"]:
            # Check if all questions were processed
            all_processed = all(
                q.status in (QuestionStatus.VERIFIED, QuestionStatus.UNVERIFIED, QuestionStatus.FAILED, QuestionStatus.COMPLETED)
                for q in questions
            )
            if all_processed:
                self.job_mgr.data["status"] = JobStatus.COMPLETED
            self.job_mgr.save()
            return compile_result
        else:
            self.job_mgr.data["status"] = JobStatus.FAILED
            self.job_mgr.save()
            return compile_result


def execute_job(
    pdf_path: str,
    job_id: Optional[str] = None,
    exam_metadata: Optional[Dict[str, str]] = None,
    dpi: int = 200,
) -> Dict[str, Any]:
    """
    Convenience functional interface to run or resume a full exam solving job.
    Returns complete final summary.
    """
    mgr = JobManager(job_id=job_id, source_pdf=pdf_path)
    wf = ExamWorkflow(mgr)
    
    # 1. OCR & Transcription
    wf.run_ocr_and_transcription(dpi=dpi)
    
    # 2. Solve & Verify
    wf.solve_all_questions(interactive=False)
    
    # 3. Assemble & Compile
    wf.assemble_latex_and_compile(exam_metadata=exam_metadata)
    
    return mgr.get_summary()
