"""
LaTeX Assembly and Compilation Engine.
Preserves the exact preamble and document structure of main.tex,
populates authorized placeholders ({problem}, {solution}, exam metadata),
and compiles valid PDFs using Tectonic or local TeX engines.
"""

import os
import re
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
import pymupdf

from exam_solver.config import config, TEMPLATE_PATH
from exam_solver.state import QuestionRecord, JobManager


HEADER_RULE = r"\noindent\rule{7in}{2.8pt}"


class LatexEngine:
    """Manages template parsing, content injection, and compilation."""

    def __init__(self, template_path: Path = TEMPLATE_PATH):
        self.template_path = Path(template_path)
        if not self.template_path.is_file():
            raise FileNotFoundError(f"Template main.tex not found at: {self.template_path}")
            
        with open(self.template_path, "r", encoding="utf-8") as f:
            self.raw_template = f.read()
            
        self._parse_template()

    def _parse_template(self):
        """Extract preamble and header from main.tex preserving all packages and typography."""
        if r"\begin{document}" not in self.raw_template:
            raise ValueError("Template does not contain \\begin{document}")
            
        parts = self.raw_template.split(r"\begin{document}", 1)
        self.preamble = parts[0].strip()
        body_and_end = parts[1]
        
        # The header is everything between \begin{document} and the first \begin{problem}
        if r"\begin{problem}" in body_and_end:
            header_raw = body_and_end.split(r"\begin{problem}", 1)[0].strip()
            # Clean trailing separator comment lines before \begin{problem}
            clean_lines = []
            for line in header_raw.splitlines():
                if line.strip().startswith("% Problem") or (line.strip().startswith("%") and len(line.strip()) > 30):
                    continue
                clean_lines.append(line)
            self.header_block = "\n".join(clean_lines).strip()
        else:
            self.header_block = body_and_end.strip()

    def sanitize_math_and_text(self, text: str) -> str:
        """
        Clean and sanitize text for LaTeX rendering.
        Preserves valid math equations and environments, but removes any stray markdown tags.
        """
        if not text:
            return ""
            
        cleaned = text.strip()
        
        # Remove markdown codeblock fences if present
        cleaned = re.sub(r"^```(?:latex|tex)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        
        # Remove any nested \begin{document} or \end{document}
        cleaned = re.sub(r"\\begin\{document\}", "", cleaned)
        cleaned = re.sub(r"\\end\{document\}", "", cleaned)
        
        # Remove duplicate \begin{solution} or \end{solution} if the solver accidentally wrapped them
        cleaned = re.sub(r"^\\begin\{solution\}\s*", "", cleaned)
        cleaned = re.sub(r"\s*\\end\{solution\}$", "", cleaned)
        
        return cleaned.strip()

    def generate_latex(
        self,
        questions: List[QuestionRecord],
        exam_metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Assemble the complete LaTeX document using ONLY authorized placeholders.
        """
        meta = exam_metadata or {}
        exam_type_year = meta.get("exam_type_year", "*UTS/UAS* *Tahun*")
        course_name = meta.get("course_name", "*AS/FIxxxx* - *Nama Mata Kuliah*")
        semester = meta.get("semester", "*Ganjil/Genap*")
        
        # Controlled replacement of metadata fields in header
        custom_header = self.header_block
        custom_header = custom_header.replace(r"\textbf{*UTS/UAS* *Tahun*}", f"\\textbf{{{exam_type_year}}}")
        custom_header = custom_header.replace(r"*UTS/UAS* *Tahun*", exam_type_year)
        
        custom_header = custom_header.replace(r"*AS/FIxxxx* - *Nama Mata Kuliah*", course_name)
        custom_header = custom_header.replace(r"*Ganjil/Genap*", semester)
        
        # Assemble problem and solution blocks
        doc_blocks = [
            self.preamble,
            r"\begin{document}",
            custom_header,
        ]
        
        for idx, q in enumerate(questions):
            is_last = (idx == len(questions) - 1)
            q_num = str(q.q_id)
            problem_text = self.sanitize_math_and_text(q.cleaned_text)
            
            # Select final solution or the best attempt
            solution_text = q.final_solution
            if not solution_text and q.solutions:
                solution_text = q.solutions[-1].get("solution_latex", "")
            if not solution_text:
                solution_text = r"\textit{Solusi belum tersedia atau proses penyelesaian gagal.}"
                
            solution_text = self.sanitize_math_and_text(solution_text)
            
            block_lines = [
                "",
                "%" * 73,
                f"% Problem {q_num}",
                "%" * 120,
                f"\\begin{{problem}}{{{q_num}}}",
                problem_text,
                r"\end{problem}",
                "",
                r"\begin{solution}",
                solution_text,
                r"\end{solution}",
                HEADER_RULE,
            ]
            
            if not is_last:
                block_lines.append(r"\pagebreak")
                
            doc_blocks.append("\n".join(block_lines))
            
        doc_blocks.append("\n\\end{document}\n")
        return "\n".join(doc_blocks)

    def compile(self, tex_path: Path, output_dir: Optional[Path] = None, job_mgr: Optional[JobManager] = None) -> Dict[str, Any]:
        """
        Compile LaTeX file to PDF using Tectonic (or pdflatex/xelatex).
        Validates that PDF exists and opens properly.
        """
        tex_path = Path(tex_path).resolve()
        if not tex_path.is_file():
            raise FileNotFoundError(f"LaTeX source file not found: {tex_path}")
            
        out_dir = Path(output_dir).resolve() if output_dir else tex_path.parent
        pdf_path = out_dir / f"{tex_path.stem}.pdf"
        log_path = out_dir / "compile.log"
        
        engine = config.latex_engine or "tectonic"
        
        if "tectonic" in str(engine).lower():
            # Tectonic command: tectonic <tex_path> --outdir <out_dir>
            cmd = [str(engine), str(tex_path), "--outdir", str(out_dir)]
        else:
            # pdflatex or xelatex command
            cmd = [
                str(engine),
                "-interaction=nonstopmode",
                f"-output-directory={str(out_dir)}",
                str(tex_path)
            ]
            
        if job_mgr:
            job_mgr.log_event("COMPILATION", f"Running LaTeX compiler: {' '.join(cmd)}")
            
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            
            compile_log = res.stdout
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(compile_log)
                
            if res.returncode != 0:
                err_msg = f"LaTeX compilation failed with exit code {res.returncode}."
                if job_mgr:
                    job_mgr.log_event("COMPILATION", f"{err_msg} Check compile.log for details.", level="ERROR")
                return {
                    "success": False,
                    "exit_code": res.returncode,
                    "pdf_path": None,
                    "log_path": str(log_path),
                    "error": err_msg,
                }
                
            # Verify PDF exists and has pages
            if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
                raise FileNotFoundError(f"Compiler returned 0 but PDF not found or empty: {pdf_path}")
                
            doc = pymupdf.open(str(pdf_path))
            pages = len(doc)
            doc.close()
            
            if job_mgr:
                job_mgr.log_event("COMPILATION", f"PDF successfully compiled: {pdf_path.name} ({pages} pages, {pdf_path.stat().st_size} bytes).")
                
            return {
                "success": True,
                "exit_code": 0,
                "pdf_path": str(pdf_path),
                "page_count": pages,
                "file_size": pdf_path.stat().st_size,
                "log_path": str(log_path),
                "error": None,
            }
            
        except subprocess.TimeoutExpired:
            err_msg = "LaTeX compilation timed out after 180 seconds."
            if job_mgr:
                job_mgr.log_event("COMPILATION", err_msg, level="ERROR")
            return {
                "success": False,
                "exit_code": -1,
                "pdf_path": None,
                "log_path": str(log_path),
                "error": err_msg,
            }
        except Exception as e:
            err_msg = f"LaTeX compilation error: {str(e)}"
            if job_mgr:
                job_mgr.log_event("COMPILATION", err_msg, level="ERROR")
            return {
                "success": False,
                "exit_code": -1,
                "pdf_path": None,
                "log_path": str(log_path),
                "error": err_msg,
            }
