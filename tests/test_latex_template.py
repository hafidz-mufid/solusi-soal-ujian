"""
Unit and integration tests for LaTeX template parsing, placeholder replacement,
and compilation error handling.
"""

import shutil
import tempfile
from pathlib import Path
import pytest
import pymupdf

from exam_solver.latex_engine import LatexEngine
from exam_solver.state import QuestionRecord, JobManager
from exam_solver.config import TEMPLATE_PATH


def test_template_exists_and_preamble_parsed():
    """Verify that main.tex is loaded and its complete preamble is preserved."""
    assert TEMPLATE_PATH.is_file(), "main.tex must exist in project root"
    engine = LatexEngine(TEMPLATE_PATH)
    
    # Check that critical packages and definitions in main.tex remain intact
    assert r"\documentclass[a4paper, 11pt]{article}" in engine.preamble
    assert r"\usepackage{mdframed}" in engine.preamble
    assert r"\usepackage{stix2}" in engine.preamble
    assert r"\usepackage[most]{tcolorbox}" in engine.preamble
    assert r"\newenvironment{problem}" in engine.preamble
    assert r"\newenvironment{solution}" in engine.preamble


def test_authorized_placeholder_replacement():
    """Verify that only authorized placeholders are replaced without corrupting formatting."""
    engine = LatexEngine(TEMPLATE_PATH)
    
    q1 = QuestionRecord(
        q_id="1",
        title="Soal 1",
        raw_text=r"Tentukan jari-jari Schwarzschild untuk massa matahari $M_\odot$.",
        cleaned_text=r"Tentukan jari-jari Schwarzschild untuk massa matahari $M_\odot$.",
        page_number=1,
        final_solution=r"Jari-jari Schwarzschild dinyatakan dengan: \begin{equation*} \boxed{R_s = \frac{2GM_\odot}{c^2} \approx 2.95\text{ km}} \end{equation*}"
    )
    
    metadata = {
        "exam_type_year": "UAS 2024",
        "course_name": "AS2005 - Astrofisika Teoretik",
        "semester": "Semester Ganjil",
    }
    
    latex_output = engine.generate_latex([q1], exam_metadata=metadata)
    
    # Check authorized metadata replacements
    assert "UAS 2024" in latex_output
    assert "AS2005 - Astrofisika Teoretik" in latex_output
    assert "Semester Ganjil" in latex_output
    
    # Check authorized question environment
    assert r"\begin{problem}{1}" in latex_output
    assert r"Tentukan jari-jari Schwarzschild" in latex_output
    assert r"\begin{solution}" in latex_output
    assert r"R_s = \frac{2GM_\odot}{c^2}" in latex_output
    assert r"\end{document}" in latex_output


def test_latex_compilation_and_pdf_validation(tmp_path):
    """Test actual compilation with Tectonic and verify generated PDF."""
    engine = LatexEngine(TEMPLATE_PATH)
    
    q = QuestionRecord(
        q_id="1",
        title="Soal 1",
        raw_text="Hitung energi foton dengan frekuensi $\\nu = 5 \\times 10^{14}$ Hz.",
        cleaned_text="Hitung energi foton dengan frekuensi $\\nu = 5 \\times 10^{14}$ Hz.",
        page_number=1,
        final_solution=r"Energi foton Planck: \begin{equation*} \boxed{E = h\nu = 3.31 \times 10^{-19}\text{ J}} \end{equation*}"
    )
    
    tex_str = engine.generate_latex([q], {"exam_type_year": "UTS 2024", "course_name": "Fisika Modern", "semester": "Semester Genap"})
    tex_file = tmp_path / "test_exam.tex"
    tex_file.write_text(tex_str, encoding="utf-8")
    
    res = engine.compile(tex_file, output_dir=tmp_path)
    assert res["success"] is True, f"Compilation failed: {res.get('error')}"
    assert res["exit_code"] == 0
    assert Path(res["pdf_path"]).is_file()
    assert res["page_count"] >= 1
    assert res["file_size"] > 1000


def test_latex_compilation_error_handling(tmp_path):
    """Verify that syntax errors in solutions are caught without crashing."""
    engine = LatexEngine(TEMPLATE_PATH)
    
    # Deliberately broken LaTeX syntax (unclosed environment)
    broken_tex = engine.preamble + "\n\\begin{document}\n\\begin{equation*}\nx = 1\n\\end{document}\n"
    tex_file = tmp_path / "broken_exam.tex"
    tex_file.write_text(broken_tex, encoding="utf-8")
    
    res = engine.compile(tex_file, output_dir=tmp_path)
    assert res["success"] is False
    assert res["exit_code"] != 0
    assert Path(res["log_path"]).is_file()
