"""
Gemini Solver Client.
Interfaces with Google Gemini (default: gemini-3.8-flash) to solve
astronomy, physics, mathematics, and numerical questions with auditable derivations.
"""

import base64
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
import requests

from exam_solver.config import config, mask_key, DEFAULT_GEMINI_MODEL
from exam_solver.state import JobManager


SOLVER_SYSTEM_PROMPT = """Anda adalah ahli astrofisika, fisika teoritik, dan komputasi sains.
Tugas Anda adalah menyelesaikan soal ujian tingkat sarjana astronomi/fisika secara mendalam, tepat, dan dapat diaudit secara ilmiah.

Pedoman pengerjaan:
1. Identifikasi secara eksplisit apa yang diketahui (given) dan apa yang ditanyakan (asked).
2. Tentukan metode/hukum fisika dan teorema matematika yang tepat.
3. Tuliskan setiap penurunan rumus secara lengkap, substitusi nilai numerik beserta satuannya.
4. Periksa konsistensi dimensi dan satuan (dimensional analysis).
5. Jangan pernah mengarang konstanta atau data. Gunakan konstanta standar astronomi/fisika (CODATA / IAU).
6. Format output LaTeX harus langsung kompatibel dengan template:
   - Gunakan itemize bertahap jika soal memiliki sub-nomor: \\begin{itemize} \\item[a.] ... \\item[b.] ... \\end{itemize}
   - Gunakan environment matematika yang bersih: \\begin{equation*} ... \\end{equation*} atau \\begin{split} ... \\end{split}
   - Kotakkan jawaban akhir menggunakan \\boxed{...}
   - Gunakan simbol standar: \\rho, \\mu_0, \\omega, \\vec{B}, \\vec{J}, dll.
   - Jangan gunakan format markdown codeblock ```latex di awal/akhir jawaban, hasilkan langsung sintaks LaTeX solusi.
"""


def encode_image_base64(image_path: Path) -> str:
    """Encode an image file to base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


class GeminiSolver:
    """Client for Google Gemini solver endpoint."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or config.gemini_api_key
        self.model = model or config.gemini_model or DEFAULT_GEMINI_MODEL
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    def solve_question(
        self,
        question_text: str,
        image_path: Optional[Path] = None,
        previous_solution: Optional[str] = None,
        verifier_feedback: Optional[str] = None,
        user_clarification: Optional[str] = None,
        job_mgr: Optional[JobManager] = None,
    ) -> Dict[str, Any]:
        """
        Submit a question to Gemini and receive a structured candidate solution.
        Supports both initial solution and revision with verifier feedback.
        """
        if not self.api_key:
            masked = mask_key(self.api_key)
            raise ValueError(f"GEMINI_API_KEY is not configured (current: {masked}). Please set it in .env.")

        # Construct prompt
        prompt_parts = []
        if previous_solution and verifier_feedback:
            # Revision prompt
            prompt_text = (
                f"{SOLVER_SYSTEM_PROMPT}\n\n"
                f"=== SOAL UJIAN ===\n{question_text}\n\n"
            )
            if user_clarification:
                prompt_text += f"=== KLARIFIKASI PENGGUNA TERKAIT AMBIGUITAS ===\n{user_clarification}\n\n"
            prompt_text += (
                f"=== SOLUSI SEBELUMNYA YANG DINILAI SALAH/KURANG TEPAT ===\n{previous_solution}\n\n"
                f"=== CATATAN KRITIK & KOREKSI DARI VERIFIKATOR INDEPENDEN ===\n{verifier_feedback}\n\n"
                f"Instruksi Perbaikan:\n"
                f"Perbaiki seluruh kesalahan rumus, aljabar, substitusi, atau analisis dimensi "
                f"yang ditemukan verifikator di atas. Hasilkan solusi baru yang lengkap dan benar."
            )
        else:
            # Initial solution prompt
            prompt_text = (
                f"{SOLVER_SYSTEM_PROMPT}\n\n"
                f"=== SOAL UJIAN ===\n{question_text}\n\n"
            )
            if user_clarification:
                prompt_text += f"=== KLARIFIKASI PENGGUNA TERKAIT AMBIGUITAS ===\n{user_clarification}\n\n"
            prompt_text += "Hasilkan solusi lengkap dan rinci dalam sintaks LaTeX yang siap disisipkan."

        parts_payload: List[Dict[str, Any]] = [{"text": prompt_text}]

        # Add image if provided (e.g. for questions with diagrams)
        if image_path and Path(image_path).is_file():
            try:
                b64_data = encode_image_base64(Path(image_path))
                parts_payload.append({
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": b64_data,
                    }
                })
            except Exception as e:
                if job_mgr:
                    job_mgr.log_event("SOLVER", f"Failed to attach image {image_path}: {str(e)}", level="WARNING")

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts_payload
                }
            ],
            "generationConfig": {
                "temperature": 0.15,
                "maxOutputTokens": 8192,
            }
        }

        url = f"{self.base_url}?key={self.api_key}"
        headers = {"Content-Type": "application/json"}

        # Bounded exponential backoff retry loop
        max_retries = config.max_retries
        backoff = 2.0
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                start_time = time.time()
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=config.timeout
                )
                elapsed = time.time() - start_time

                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        raise ValueError(f"Gemini API returned no candidates: {data}")

                    content_parts = candidates[0].get("content", {}).get("parts", [])
                    text_parts = [p.get("text", "") for p in content_parts if "text" in p]
                    full_text = "\n".join(text_parts).strip()

                    # Strip any surrounding markdown codeblocks if accidentally returned
                    if full_text.startswith("```latex"):
                        full_text = full_text[len("```latex"):].strip()
                    if full_text.startswith("```"):
                        full_text = full_text[len("```"):].strip()
                    if full_text.endswith("```"):
                        full_text = full_text[:-len("```")].strip()

                    usage = data.get("usageMetadata", {})
                    tokens_in = usage.get("promptTokenCount", 0)
                    tokens_out = usage.get("candidatesTokenCount", 0)

                    if job_mgr:
                        job_mgr.record_api_call("gemini", tokens_in, tokens_out)
                        job_mgr.log_event("SOLVER", f"Gemini ({self.model}) response received in {elapsed:.2f}s ({tokens_in} prompt tokens, {tokens_out} candidates tokens).")

                    return {
                        "success": True,
                        "solution_latex": full_text,
                        "model": self.model,
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "elapsed_seconds": elapsed,
                        "error": None,
                    }

                elif response.status_code in (429, 500, 503, 504):
                    # Rate limit or server error: retry with backoff
                    wait_time = backoff ** attempt
                    if job_mgr:
                        job_mgr.log_event("SOLVER", f"Gemini returned status {response.status_code}. Retrying in {wait_time:.1f}s (attempt {attempt}/{max_retries}).", level="WARNING")
                    time.sleep(wait_time)
                elif response.status_code in (400, 401, 403):
                    # Client or authentication error: do not retry indefinitely
                    err_msg = f"Gemini API authentication/client error ({response.status_code}): {response.text}"
                    if job_mgr:
                        job_mgr.log_event("SOLVER", err_msg, level="ERROR")
                    raise PermissionError(err_msg)
                else:
                    response.raise_for_status()

            except (requests.Timeout, requests.ConnectionError) as net_err:
                last_error = net_err
                wait_time = backoff ** attempt
                if job_mgr:
                    job_mgr.log_event("SOLVER", f"Network error contacting Gemini: {str(net_err)}. Retrying in {wait_time:.1f}s.", level="WARNING")
                time.sleep(wait_time)
            except Exception as e:
                last_error = e
                if not isinstance(e, (requests.Timeout, requests.ConnectionError)):
                    raise

        raise RuntimeError(f"Gemini API call failed after {max_retries} attempts: {str(last_error)}")
