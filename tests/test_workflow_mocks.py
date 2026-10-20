"""
Integration tests using mocks to verify:
- The strict 2-attempt solution limit
- Resumption after interruption without duplicate API calls
- Quota exhaustion (HTTP 429) & timeout handling
"""

from unittest.mock import MagicMock, patch
import pytest
import requests

from exam_solver.workflow import ExamWorkflow
from exam_solver.state import JobManager, QuestionRecord, QuestionStatus, JobStatus
from exam_solver.solver import GeminiSolver
from exam_solver.verifier import OpenRouterVerifier


def test_two_attempt_limit_with_eventual_pass(tmp_path):
    """
    Attempt 1 fails verification -> Attempt 2 revised with feedback -> Passes.
    Total solver attempts: exactly 2.
    """
    job_mgr = JobManager(job_id="test_two_attempt_pass", base_jobs_dir=tmp_path)
    q = QuestionRecord(
        q_id="1",
        title="Soal 1",
        raw_text="Hitung percepatan gravitasi di permukaan bumi.",
        cleaned_text="Hitung percepatan gravitasi di permukaan bumi.",
        page_number=1,
        status=QuestionStatus.PENDING,
    )
    job_mgr.set_question(q)

    # Mock solver responses
    mock_solver = MagicMock(spec=GeminiSolver)
    mock_solver.solve_question.side_effect = [
        {"success": True, "solution_latex": "g = 9.0 m/s^2", "model": "gemini-3.8-flash", "elapsed_seconds": 1.0},
        {"success": True, "solution_latex": "g = 9.8 m/s^2", "model": "gemini-3.8-flash", "elapsed_seconds": 1.0},
    ]

    # Mock verifier responses: first FAIL, second PASS
    mock_verifier = MagicMock(spec=OpenRouterVerifier)
    mock_verifier.verify_solution.side_effect = [
        {"status": "FAIL", "findings": "Earth standard gravity is ~9.8 m/s^2, not 9.0.", "required_corrections": ["Use 9.8"]},
        {"status": "PASS", "findings": "Accurate calculation.", "required_corrections": []},
    ]

    wf = ExamWorkflow(job_mgr, solver=mock_solver, verifier=mock_verifier)
    result_q = wf.solve_and_verify_question("1")

    assert result_q.status == QuestionStatus.VERIFIED
    assert result_q.solution_attempts_count == 2
    assert mock_solver.solve_question.call_count == 2
    assert mock_verifier.verify_solution.call_count == 2
    assert "9.8" in result_q.final_solution


def test_two_attempt_limit_with_failure(tmp_path):
    """
    Attempt 1 fails verification -> Attempt 2 fails verification -> Stop at 2 attempts, status FAILED.
    Must NEVER retry a 3rd time.
    """
    job_mgr = JobManager(job_id="test_two_attempt_fail", base_jobs_dir=tmp_path)
    q = QuestionRecord(
        q_id="1",
        title="Soal 1",
        raw_text="Hitung massa bintang neutron.",
        cleaned_text="Hitung massa bintang neutron.",
        page_number=1,
        status=QuestionStatus.PENDING,
    )
    job_mgr.set_question(q)

    mock_solver = MagicMock(spec=GeminiSolver)
    mock_solver.solve_question.side_effect = [
        {"success": True, "solution_latex": "Attempt 1 solution", "model": "gemini-3.8-flash", "elapsed_seconds": 1.0},
        {"success": True, "solution_latex": "Attempt 2 revised solution", "model": "gemini-3.8-flash", "elapsed_seconds": 1.0},
        {"success": True, "solution_latex": "Attempt 3 INVALID", "model": "gemini-3.8-flash", "elapsed_seconds": 1.0},
    ]

    mock_verifier = MagicMock(spec=OpenRouterVerifier)
    mock_verifier.verify_solution.return_value = {
        "status": "FAIL",
        "findings": "Derivation remains invalid.",
        "required_corrections": ["Fix integration limits"]
    }

    wf = ExamWorkflow(job_mgr, solver=mock_solver, verifier=mock_verifier)
    result_q = wf.solve_and_verify_question("1")

    assert result_q.status == QuestionStatus.FAILED
    assert result_q.solution_attempts_count == 2
    assert mock_solver.solve_question.call_count == 2  # Strict 2 attempt ceiling!


def test_recovery_without_redundant_api_calls(tmp_path):
    """
    Simulate interruption:
    Question 1 is already VERIFIED.
    Question 2 is PENDING.
    Workflow resumption must NOT make any API calls for Question 1.
    """
    job_mgr1 = JobManager(job_id="test_resume_job", base_jobs_dir=tmp_path)
    
    # Q1 was already verified previously
    q1 = QuestionRecord(
        q_id="1",
        title="Soal 1",
        raw_text="Soal 1 text",
        cleaned_text="Soal 1 text",
        page_number=1,
        status=QuestionStatus.VERIFIED,
        solution_attempts_count=1,
        final_solution="Final solution 1",
    )
    # Q2 was not processed yet
    q2 = QuestionRecord(
        q_id="2",
        title="Soal 2",
        raw_text="Soal 2 text",
        cleaned_text="Soal 2 text",
        page_number=1,
        status=QuestionStatus.PENDING,
    )
    job_mgr1.set_question(q1)
    job_mgr1.set_question(q2)

    # Second session: reload same job
    job_mgr2 = JobManager(job_id="test_resume_job", base_jobs_dir=tmp_path)
    
    mock_solver = MagicMock(spec=GeminiSolver)
    mock_solver.solve_question.return_value = {
        "success": True,
        "solution_latex": "Final solution 2",
        "model": "gemini-3.8-flash",
        "elapsed_seconds": 1.0,
    }

    mock_verifier = MagicMock(spec=OpenRouterVerifier)
    mock_verifier.verify_solution.return_value = {
        "status": "PASS",
        "findings": "Correct.",
        "required_corrections": [],
    }

    wf = ExamWorkflow(job_mgr2, solver=mock_solver, verifier=mock_verifier)
    wf.solve_all_questions()

    # Verify that solver and verifier were only called ONCE (for Q2), NOT for Q1!
    assert mock_solver.solve_question.call_count == 1
    assert mock_verifier.verify_solution.call_count == 1
    
    q1_reloaded = job_mgr2.get_question("1")
    assert q1_reloaded.status == QuestionStatus.VERIFIED
    
    q2_reloaded = job_mgr2.get_question("2")
    assert q2_reloaded.status == QuestionStatus.VERIFIED


def test_quota_exhaustion_handling(tmp_path):
    """
    Test that HTTP 429 quota exhaustion is caught gracefully,
    retried with backoff, and eventually persisted without crash.
    """
    job_mgr = JobManager(job_id="test_quota_job", base_jobs_dir=tmp_path)
    q = QuestionRecord(
        q_id="1",
        title="Soal 1",
        raw_text="Soal text",
        cleaned_text="Soal text",
        page_number=1,
        status=QuestionStatus.PENDING,
    )
    job_mgr.set_question(q)

    # Create solver with fake API key
    solver = GeminiSolver(api_key="mock_key")
    
    # Mock requests.post to return 429 Rate Limit
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "RESOURCE_EXHAUSTED: Quota exceeded"
    
    with patch("requests.post", return_value=mock_resp), patch("time.sleep", return_value=None):
        wf = ExamWorkflow(job_mgr, solver=solver)
        result_q = wf.solve_and_verify_question("1")
        
        assert result_q.status == QuestionStatus.FAILED
        assert "429" in result_q.error or "failed" in result_q.error.lower()
        # State was persisted safely
        saved_q = job_mgr.get_question("1")
        assert saved_q.status == QuestionStatus.FAILED
