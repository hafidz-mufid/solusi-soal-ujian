"""
Unit tests for verifier response JSON parsing, validation, and status handling.
"""

from exam_solver.verifier import extract_json_from_response, OpenRouterVerifier
from exam_solver.state import QuestionStatus, JobManager


def test_extract_pure_json():
    raw = '{"status": "PASS", "findings": "All derivations are accurate.", "required_corrections": []}'
    parsed = extract_json_from_response(raw)
    assert parsed is not None
    assert parsed["status"] == "PASS"
    assert "accurate" in parsed["findings"]


def test_extract_markdown_fenced_json():
    raw = """
Here is my review evaluation:
```json
{
  "status": "FAIL",
  "findings": "Missing factor of 1/2 in kinetic energy term.",
  "required_corrections": ["Multiply kinetic energy by 0.5"]
}
```
Hope this helps.
"""
    parsed = extract_json_from_response(raw)
    assert parsed is not None
    assert parsed["status"] == "FAIL"
    assert len(parsed["required_corrections"]) == 1


def test_malformed_json_fallback(tmp_path):
    job_mgr = JobManager(job_id="test_malformed_v", base_jobs_dir=tmp_path)
    verifier = OpenRouterVerifier(api_key="mock_key")
    
    # Test extract_json_from_response directly on broken string
    broken_str = "Status is PASS, but this is plain prose without braces."
    parsed = extract_json_from_response(broken_str)
    assert parsed is None


def test_missing_api_key_returns_unverified(tmp_path):
    """Verify that when OpenRouter key is empty, it returns UNVERIFIED and never PASS."""
    job_mgr = JobManager(job_id="test_missing_key_v", base_jobs_dir=tmp_path)
    verifier = OpenRouterVerifier(api_key="")
    
    res = verifier.verify_solution("Question text", "Solution text", job_mgr=job_mgr)
    assert res["status"] == "UNVERIFIED"
    assert res["status"] != "PASS"
    assert "not configured" in res["findings"].lower()
