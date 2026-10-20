"""
Configuration and Environment Management for Exam Solver.
Safely loads API credentials, validates external tools, and prevents secret leakage.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Automatically load .env if present
load_dotenv()

# Base project paths
BASE_DIR = Path(__file__).resolve().parent.parent
JOBS_DIR = BASE_DIR / "jobs"
TEMPLATE_PATH = BASE_DIR / "main.tex"

# Default Model Specifications
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3.5-super-vl:free"

# Operational Defaults
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_RETRIES = 3
MAX_SOLVER_ATTEMPTS = 2  # As per mandatory requirement: max 2 solution attempts


def mask_key(key: Optional[str]) -> str:
    """Safely mask an API key for logs and UI display without leaking credentials."""
    if not key or not isinstance(key, str):
        return "<NOT_CONFIGURED>"
    stripped = key.strip()
    if len(stripped) <= 8:
        return "****"
    return f"{stripped[:4]}...{stripped[-4:]}"


def find_tesseract() -> Optional[Path]:
    """Locate Tesseract executable on the system or local directory."""
    # 1. Environment variable
    env_path = os.getenv("TESSERACT_CMD")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    
    # 2. System PATH
    which_tess = shutil.which("tesseract")
    if which_tess:
        return Path(which_tess)
        
    # 3. Known extraction and installation locations on Windows
    candidate_paths = [
        Path.home() / "tesseract" / "tesseract.exe",
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
        Path.home() / "AppData/Local/Programs/Tesseract-OCR/tesseract.exe",
    ]
    for p in candidate_paths:
        if p.is_file():
            return p
    return None


def find_tessdata(tesseract_exe: Optional[Path]) -> Optional[Path]:
    """Locate Tesseract tessdata directory."""
    env_data = os.getenv("TESSDATA_PREFIX")
    if env_data and Path(env_data).is_dir():
        return Path(env_data)
    if tesseract_exe:
        data_dir = tesseract_exe.parent / "tessdata"
        if data_dir.is_dir():
            return data_dir
    return None


def find_latex_engine() -> Optional[str]:
    """Locate an available LaTeX compilation engine (Tectonic, pdflatex, xelatex)."""
    env_engine = os.getenv("LATEX_ENGINE")
    if env_engine and shutil.which(env_engine):
        return env_engine
        
    # Check tectonic first (self-contained, robust)
    if shutil.which("tectonic"):
        return "tectonic"
    if shutil.which("pdflatex"):
        return "pdflatex"
    if shutil.which("xelatex"):
        return "xelatex"
        
    # Check known paths for tectonic
    candidate_tectonic = Path.home() / ".gemini/antigravity/bin/tectonic.exe"
    if candidate_tectonic.is_file():
        return str(candidate_tectonic)
        
    return None


class Config:
    """System configuration container."""
    
    def __init__(self):
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.gemini_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL).strip()
        
        self.timeout = int(os.getenv("API_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
        self.max_retries = int(os.getenv("API_MAX_RETRIES", DEFAULT_MAX_RETRIES))
        
        self.tesseract_cmd = find_tesseract()
        self.tessdata_prefix = find_tessdata(self.tesseract_cmd)
        self.latex_engine = find_latex_engine()
        
        # Configure pytesseract global settings if found
        if self.tesseract_cmd:
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = str(self.tesseract_cmd)
                if self.tessdata_prefix:
                    os.environ["TESSDATA_PREFIX"] = str(self.tessdata_prefix)
            except ImportError:
                pass

    def validate(self) -> Dict[str, Any]:
        """Validate the environment and return a structured diagnostic status report."""
        status = {
            "gemini_api_key_configured": bool(self.gemini_api_key),
            "gemini_api_key_masked": mask_key(self.gemini_api_key),
            "gemini_model": self.gemini_model,
            "openrouter_api_key_configured": bool(self.openrouter_api_key),
            "openrouter_api_key_masked": mask_key(self.openrouter_api_key),
            "openrouter_model": self.openrouter_model,
            "tesseract_available": self.tesseract_cmd is not None,
            "tesseract_path": str(self.tesseract_cmd) if self.tesseract_cmd else None,
            "tessdata_prefix": str(self.tessdata_prefix) if self.tessdata_prefix else None,
            "latex_engine_available": self.latex_engine is not None,
            "latex_engine": self.latex_engine,
            "template_exists": TEMPLATE_PATH.is_file(),
        }
        return status


# Singleton default config instance
config = Config()
