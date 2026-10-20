"""
Security and credential protection tests.
Verifies API key masking, safe error reporting, and .gitignore coverage.
"""

from pathlib import Path
import pytest

from exam_solver.config import mask_key, BASE_DIR
from exam_solver.solver import GeminiSolver


def test_mask_key():
    """Verify masking logic protects secrets."""
    # Standard length key
    assert mask_key("AIzaSyD-1234567890abcdef") == "AIza...cdef"
    assert "1234567890" not in mask_key("AIzaSyD-1234567890abcdef")
    
    # Short keys
    assert mask_key("short") == "****"
    assert mask_key("") == "<NOT_CONFIGURED>"
    assert mask_key(None) == "<NOT_CONFIGURED>"


def test_solver_missing_key_does_not_leak():
    """Verify solver raises descriptive error without leaking dummy secrets."""
    solver = GeminiSolver(api_key="")
    with pytest.raises(ValueError) as excinfo:
        solver.solve_question("Question text")
    err = str(excinfo.value)
    assert "GEMINI_API_KEY is not configured" in err
    assert "<NOT_CONFIGURED>" in err


def test_gitignore_contains_env_and_secrets():
    """Verify that .env is in .gitignore."""
    gitignore_path = BASE_DIR / ".gitignore"
    assert gitignore_path.is_file(), ".gitignore must exist"
    
    content = gitignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    
    assert ".env" in lines, ".env must be explicitly excluded in .gitignore"
    assert "jobs/" in lines, "jobs/ working directory must be in .gitignore"
