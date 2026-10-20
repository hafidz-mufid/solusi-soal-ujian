"""
OpenRouter Verifier Client.
Interfaces with OpenRouter (default: nvidia/nemotron-3.5-super-vl:free)
to provide rigorous, independent verification of candidate solutions.
"""

import json
import re
import time
from typing import Dict, Any, Optional, List
import requests

from exam_solver.config import config, mask_key, DEFAULT_OPENROUTER_MODEL
from exam_solver.state import JobManager


VERIFIER_SYSTEM_PROMPT = """You are a rigorous, independent scientific and mathematical peer-reviewer for undergraduate astronomy and physics exam solutions.

Your role is to strictly verify a candidate solution against the exam question.
You must inspect:
1. Question Alignment: Does the solution answer every question part asked?
2. Method & Physics: Are the physical principles, conservation laws, and formulas appropriate and correctly applied?
3. Mathematical Derivations: Are the algebraic operations, steps, integrals, and vector transformations rigorous and correct?
4. Arithmetic & Substitutions: Are numeric values, scientific notation exponents, constants, and unit conversions accurate?
5. Dimensional Consistency: Are the dimensions consistent throughout the equations?
6. Assumptions: Are all unstated assumptions stated, justifiable, and sound?

You MUST respond strictly with a valid JSON object formatted as follows:
{
  "status": "PASS" | "FAIL" | "UNCERTAIN",
  "findings": "<Detailed critique explaining either why the solution is fully correct or identifying specific errors>",
  "required_corrections": [
    "<Specific correction 1 if status is FAIL or UNCERTAIN>",
    "<Specific correction 2>"
  ]
}

Rules for status:
- "PASS": Only if the derivations, formula, arithmetic, and units are scientifically sound and answer the question correctly.
- "FAIL": If there is an algebraic mistake, wrong formula, arithmetic error, unit mismatch, or incorrect final answer.
- "UNCERTAIN": If information in the question is incomplete or ambiguous preventing definitive verification.
"""


def extract_json_from_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """Robustly extract and parse JSON from model output."""
    if not raw_text:
        return None
        
    cleaned = raw_text.strip()
    
    # 1. Strip markdown codeblocks
    if "```json" in cleaned:
        m = re.search(r"```json\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()
    elif "```" in cleaned:
        m = re.search(r"```\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()
            
    # 2. Try direct JSON parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
        
    # 3. Find outermost braces { ... }
    m_braces = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if m_braces:
        try:
            return json.loads(m_braces.group(1))
        except json.JSONDecodeError:
            pass
            
    return None


class OpenRouterVerifier:
    """Client for OpenRouter verification endpoint."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or config.openrouter_api_key
        self.model = model or config.openrouter_model or DEFAULT_OPENROUTER_MODEL
        self.endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def verify_solution(
        self,
        question_text: str,
        candidate_solution: str,
        job_mgr: Optional[JobManager] = None,
    ) -> Dict[str, Any]:
        """
        Submit question and candidate solution to OpenRouter for independent verification.
        Returns validated result: {"status": "PASS"|"FAIL"|"UNCERTAIN"|"UNVERIFIED", "findings": ..., "required_corrections": ...}
        """
        if not self.api_key:
            if job_mgr:
                job_mgr.log_event("VERIFIER", "OPENROUTER_API_KEY is not configured. Marking as UNVERIFIED.", level="WARNING")
            return {
                "status": "UNVERIFIED",
                "findings": "OpenRouter API key is not configured. Candidate solution could not be independently verified.",
                "required_corrections": [],
                "model": self.model,
                "error": "Missing OPENROUTER_API_KEY",
            }

        user_content = (
            f"=== EXAM QUESTION ===\n{question_text}\n\n"
            f"=== CANDIDATE SOLUTION (LATEX) ===\n{candidate_solution}\n\n"
            f"Please review this candidate solution thoroughly and output your evaluation JSON."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/solusi-soal-ujian",
            "X-Title": "Automated Exam Solver",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        max_retries = config.max_retries
        backoff = 2.0
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                start_time = time.time()
                response = requests.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=config.timeout,
                )
                elapsed = time.time() - start_time

                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise ValueError(f"OpenRouter returned no choices: {data}")

                    content = choices[0].get("message", {}).get("content", "")
                    parsed_json = extract_json_from_response(content)

                    usage = data.get("usage", {})
                    tokens_in = usage.get("prompt_tokens", 0)
                    tokens_out = usage.get("completion_tokens", 0)

                    if job_mgr:
                        job_mgr.record_api_call("openrouter", tokens_in, tokens_out)
                        job_mgr.log_event("VERIFIER", f"OpenRouter ({self.model}) responded in {elapsed:.2f}s.")

                    if parsed_json and isinstance(parsed_json, dict):
                        raw_status = str(parsed_json.get("status", "")).upper()
                        if raw_status in ("PASS", "FAIL", "UNCERTAIN"):
                            status = raw_status
                        else:
                            status = "UNCERTAIN"
                            
                        findings = parsed_json.get("findings", "No detailed findings provided.")
                        corrections = parsed_json.get("required_corrections", [])
                        if isinstance(corrections, str):
                            corrections = [corrections]

                        return {
                            "status": status,
                            "findings": findings,
                            "required_corrections": corrections,
                            "raw_response": content,
                            "model": self.model,
                            "elapsed_seconds": elapsed,
                            "error": None,
                        }
                    else:
                        # Malformed JSON
                        if job_mgr:
                            job_mgr.log_event("VERIFIER", f"Failed to parse JSON from verifier response: {content[:100]}...", level="WARNING")
                        return {
                            "status": "UNVERIFIED",
                            "findings": f"Verifier returned unparseable output: {content}",
                            "required_corrections": [],
                            "raw_response": content,
                            "model": self.model,
                            "error": "Malformed JSON from verifier",
                        }

                elif response.status_code in (429, 500, 502, 503, 504):
                    wait_time = backoff ** attempt
                    if job_mgr:
                        job_mgr.log_event("VERIFIER", f"OpenRouter returned status {response.status_code}. Retrying in {wait_time:.1f}s.", level="WARNING")
                    time.sleep(wait_time)
                elif response.status_code in (401, 403):
                    err_msg = f"OpenRouter authentication error ({response.status_code})."
                    if job_mgr:
                        job_mgr.log_event("VERIFIER", err_msg, level="ERROR")
                    return {
                        "status": "UNVERIFIED",
                        "findings": err_msg,
                        "required_corrections": [],
                        "model": self.model,
                        "error": err_msg,
                    }
                else:
                    response.raise_for_status()

            except (requests.Timeout, requests.ConnectionError) as net_err:
                last_error = net_err
                wait_time = backoff ** attempt
                if job_mgr:
                    job_mgr.log_event("VERIFIER", f"Network error contacting OpenRouter: {str(net_err)}. Retrying in {wait_time:.1f}s.", level="WARNING")
                time.sleep(wait_time)
            except Exception as e:
                last_error = e
                break

        # If we reached here, verification failed due to outage/timeout
        err_str = str(last_error) if last_error else "Unknown verifier failure"
        if job_mgr:
            job_mgr.log_event("VERIFIER", f"OpenRouter verification unavailable: {err_str}", level="WARNING")
            
        return {
            "status": "UNVERIFIED",
            "findings": f"Verifier was unreachable or failed after retries: {err_str}",
            "required_corrections": [],
            "model": self.model,
            "error": err_str,
        }
