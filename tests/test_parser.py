"""
Unit tests for question parsing, mathematical preservation, ambiguity detection,
and transcription file formatting.
"""

from pathlib import Path
import pytest

from exam_solver.parser import parse_questions_from_pages, detect_ambiguities, generate_transcription_file
from exam_solver.state import QuestionStatus, JobManager


def test_question_splitting_and_ordering():
    """Verify that questions are extracted in correct sequential order."""
    pages = [
        {
            "page_number": 1,
            "text": (
                "Petunjuk Umum Ujian Astrofisika\n\n"
                "Soal 1\n"
                "Hitung massa jenis rata-rata bintang dengan massa M dan jejari R.\n"
                "a. Tuliskan rumus volume bola.\n"
                "b. Tentukan rumus massa jenis rho.\n\n"
                "Soal 2\n"
                "Bagaimana hubungan magnitudo semu dan fluks bolometrik?\n"
            ),
        },
        {
            "page_number": 2,
            "text": (
                "Problem 3\n"
                "Tentukan temperatur efektif sebuah bintang deret utama tipe G2V.\n"
            ),
        }
    ]
    
    questions = parse_questions_from_pages(pages)
    assert len(questions) == 3
    assert questions[0].q_id == "1"
    assert questions[0].page_number == 1
    assert "massa jenis rata-rata" in questions[0].cleaned_text
    assert "a. Tuliskan rumus" in questions[0].cleaned_text
    
    assert questions[1].q_id == "2"
    assert questions[1].page_number == 1
    
    assert questions[2].q_id == "3"
    assert questions[2].page_number == 2


def test_co_pattern_splitting():
    """Test [CO 1] format matching the user's main.tex example."""
    pages = [
        {
            "page_number": 1,
            "text": (
                "[CO 1] Jelaskan hukum pergeseran Wien.\n\n"
                "[CO 2] Bagaimana cara membuat persamaan geodesik?\n"
            ),
        }
    ]
    questions = parse_questions_from_pages(pages)
    assert len(questions) == 2
    assert questions[0].q_id == "1"
    assert questions[1].q_id == "2"


def test_ambiguity_detection():
    """Verify detection of illegible symbols, missing values, and diagram references."""
    # Test 1: Repeated question marks
    amb1 = detect_ambiguities("Berapa massa bintang ??? jika jejari 10 km?")
    assert len(amb1) > 0
    assert "Tanda tanya berulang" in amb1[0]
    
    # Test 2: Missing blank fill-in
    amb2 = detect_ambiguities("Diberikan konstanta G = _____ m^3/kg/s^2.")
    assert len(amb2) > 0
    assert "missing parameter" in amb2[0].lower()
    
    # Test 3: Diagram reference
    amb3 = detect_ambiguities("Perhatikan gambar di samping untuk menentukan arah vektor.")
    assert len(amb3) > 0
    assert "gambar" in amb3[0].lower()


def test_transcription_generation(tmp_path):
    """Verify transcription.txt format and delimiter integrity."""
    job_mgr = JobManager(job_id="test_transcription_job", base_jobs_dir=tmp_path)
    
    pages = [
        {
            "page_number": 1,
            "text": "Soal 1\nHitung luminositas matahari $L_\\odot$ jika $R = 7 \\times 10^8$ m.\n\nSoal 2\nLihat grafik di bawah untuk menentukan periode orbital."
        }
    ]
    
    questions = parse_questions_from_pages(pages)
    for q in questions:
        job_mgr.set_question(q)
        
    trans_file = generate_transcription_file(job_mgr, questions)
    assert trans_file.is_file()
    
    content = trans_file.read_text(encoding="utf-8")
    assert "TRANSKRIPSI SOAL UJIAN" in content
    assert "SOAL 1" in content
    assert "SOAL 2" in content
    assert "[PERHATIAN / AMBIGUITAS]" in content  # Because Soal 2 refers to graphic
