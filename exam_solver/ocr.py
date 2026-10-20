"""
Local OCR and PDF page processing engine.
Uses PyMuPDF (fitz) for page rendering and embedded text extraction,
and Tesseract for local, open-source OCR on scanned/photographed pages.
"""

import os
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import pymupdf  # PyMuPDF
from PIL import Image

from exam_solver.config import config
from exam_solver.state import JobManager, JobStatus


def validate_pdf(pdf_path: str) -> Dict[str, Any]:
    """Validate that the PDF exists, is non-empty, and can be opened."""
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")
    if path.stat().st_size == 0:
        raise ValueError(f"PDF file is empty (0 bytes): {pdf_path}")
        
    try:
        doc = pymupdf.open(str(path))
        page_count = len(doc)
        if page_count == 0:
            doc.close()
            raise ValueError(f"PDF file contains 0 pages: {pdf_path}")
            
        metadata = {
            "page_count": page_count,
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "format": doc.metadata.get("format", "PDF"),
            "file_size_bytes": path.stat().st_size,
        }
        doc.close()
        return metadata
    except Exception as e:
        raise ValueError(f"Failed to open or parse PDF: {str(e)}") from e


def render_pdf_pages(doc: pymupdf.Document, output_dir: Path, dpi: int = 200) -> List[Path]:
    """Render each PDF page to a PNG image in output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []
    
    # Calculate zoom matrix for the desired DPI (standard PDF is 72 dpi)
    zoom = dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)
    
    for i in range(len(doc)):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_path = output_dir / f"page_{i + 1}.png"
        pix.save(str(img_path))
        image_paths.append(img_path)
        
    return image_paths


def ocr_image(image_path: Path, lang: str = "ind+eng") -> str:
    """Perform local OCR on an image file using Tesseract."""
    import pytesseract

    # Configure tesseract executable if found by config
    if config.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = str(config.tesseract_cmd)
        
    # Check available languages
    try:
        available_langs = pytesseract.get_languages()
    except Exception:
        available_langs = []
        
    use_lang = "eng"
    if "ind" in available_langs and "eng" in available_langs:
        use_lang = "ind+eng"
    elif "eng" in available_langs:
        use_lang = "eng"
    elif available_langs:
        use_lang = available_langs[0]
        
    img = Image.open(image_path)
    text = pytesseract.image_to_string(img, lang=use_lang, config="--psm 3 --oem 3")
    return text


def extract_pdf_content(job_mgr: JobManager, dpi: int = 200) -> List[Dict[str, Any]]:
    """
    Process PDF: render pages, extract embedded text, fallback to Tesseract OCR when text is sparse.
    Saves raw output to job_mgr.raw_ocr_file.
    Returns list of page dictionaries with page number, text, image path, and extraction method.
    """
    source_pdf = job_mgr.data.get("source_pdf")
    if not source_pdf:
        raise ValueError("Job has no source PDF specified.")
        
    metadata = validate_pdf(source_pdf)
    job_mgr.data["metadata"] = metadata
    job_mgr.log_event("OCR", f"Validated PDF: {metadata['page_count']} pages, {metadata['file_size_bytes']} bytes.")
    
    doc = pymupdf.open(source_pdf)
    image_paths = render_pdf_pages(doc, job_mgr.pages_dir, dpi=dpi)
    
    pages_content = []
    raw_ocr_lines = []
    
    for idx, page in enumerate(doc):
        page_num = idx + 1
        img_path = image_paths[idx]
        
        # 1. Check embedded text
        embedded_text = page.get_text("text").strip()
        
        # A page is considered image-based if it has very few alphanumeric characters (< 40 chars)
        alpha_count = sum(1 for c in embedded_text if c.isalnum())
        
        if alpha_count >= 40:
            method = "embedded_text"
            page_text = embedded_text
            job_mgr.log_event("OCR", f"Page {page_num}: Extracted {len(page_text)} chars of digital text.")
        else:
            # Need local OCR
            method = "tesseract_ocr"
            job_mgr.log_event("OCR", f"Page {page_num}: Digital text sparse ({alpha_count} chars). Running local Tesseract OCR.")
            try:
                page_text = ocr_image(img_path)
                job_mgr.log_event("OCR", f"Page {page_num}: Local OCR complete ({len(page_text)} chars).")
            except Exception as ocr_err:
                job_mgr.log_event("OCR", f"Page {page_num}: Tesseract OCR error: {str(ocr_err)}. Using embedded text.", level="WARNING")
                page_text = embedded_text
                method = "embedded_text_fallback"
                
        pages_content.append({
            "page_number": page_num,
            "text": page_text,
            "image_path": str(img_path),
            "method": method,
        })
        
        raw_ocr_lines.append(f"--- PAGE {page_num} [{method}] ---")
        raw_ocr_lines.append(page_text)
        raw_ocr_lines.append("")
        
    doc.close()
    
    # Save raw OCR text before any corrections or parsing
    with open(job_mgr.raw_ocr_file, "w", encoding="utf-8") as f:
        f.write("\n".join(raw_ocr_lines))
        
    job_mgr.data["status"] = JobStatus.OCR_DONE
    job_mgr.save()
    job_mgr.log_event("OCR", f"Saved raw OCR output to {job_mgr.raw_ocr_file.name}.")
    
    return pages_content
