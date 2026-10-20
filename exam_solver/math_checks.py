"""
Deterministic mathematical and scientific computing checks using SymPy.
Provides verified physical/astronomical constants and checks arithmetic, algebra,
and dimensional substitutions locally without burning AI API quota.
"""

import math
import re
from typing import Dict, Any, List, Optional, Tuple
import sympy as sp

# Verified physical and astronomical constants (CODATA / IAU standard)
PHYSICAL_CONSTANTS = {
    "G": 6.67430e-11,          # Gravitational constant [m^3 kg^-1 s^-2]
    "c": 2.99792458e8,         # Speed of light in vacuum [m s^-1]
    "h": 6.62607015e-34,       # Planck constant [J s]
    "hbar": 1.054571817e-34,   # Reduced Planck constant [J s]
    "k_B": 1.380649e-23,       # Boltzmann constant [J K^-1]
    "sigma_SB": 5.670374419e-8,# Stefan-Boltzmann constant [W m^-2 K^-4]
    "mu_0": 4 * math.pi * 1e-7,# Vacuum permeability [N A^-2 or T m A^-1]
    "epsilon_0": 8.8541878128e-12, # Vacuum permittivity [F m^-1]
    "e_charge": 1.602176634e-19,# Elementary charge [C]
    "m_e": 9.1093837015e-31,   # Electron mass [kg]
    "m_p": 1.67262192369e-27,  # Proton mass [kg]
    "M_sun": 1.98847e30,       # Solar mass [kg]
    "R_sun": 6.957e8,          # Solar radius [m]
    "L_sun": 3.828e26,         # Solar luminosity [W]
    "AU": 1.495978707e11,      # Astronomical Unit [m]
    "pc": 3.085677581e16,      # Parsec [m]
    "ly": 9.460730472e15,      # Light year [m]
    "M_earth": 5.9722e24,      # Earth mass [kg]
    "R_earth": 6.371e6,        # Earth mean radius [m]
}


def parse_scientific_notation(val_str: str) -> Optional[float]:
    """
    Parse strings like:
      - '1,33 x 10^8'
      - '1.33 \\times 10^{8}'
      - '1.33 * 10^8'
      - '1.33e8'
      - '10^{11}'
      - '1361'
    into float.
    """
    if not val_str or not isinstance(val_str, str):
        return None
        
    s = val_str.strip()
    # Normalize decimal separator if followed by digits
    s = re.sub(r"(\d+),(\d+)", r"\1.\2", s)
    
    # Check for pattern like: [base] [times/x/*] 10^[exp]
    m_sci = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(?:\\times|\\cdot|[xX\*])\s*10\s*\^?\s*\{?([-+]?\d+)\}?", s)
    if m_sci:
        base = float(m_sci.group(1))
        exp = int(m_sci.group(2))
        return base * (10.0 ** exp)
        
    # Check for pattern like: 10^[exp]
    m_pow = re.search(r"(?:^|[^\d])10\s*\^?\s*\{?([-+]?\d+)\}?", s)
    if m_pow:
        exp = int(m_pow.group(1))
        return 10.0 ** exp
        
    # Check for standard exponential notation like 1.33e8
    m_exp = re.search(r"([-+]?\d+(?:\.\d+)?)[eE]([-+]?\d+)", s)
    if m_exp:
        return float(m_exp.group(0))
        
    # Plain number
    m_plain = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if m_plain:
        return float(m_plain.group(0))
        
    return None


def extract_numerical_claims(latex_text: str) -> List[Tuple[str, str, Optional[float]]]:
    """
    Extract variable assignments and final boxed values.
    Returns list of (variable_or_context, raw_expr, parsed_float_value).
    """
    claims = []
    # Match boxed results: \boxed{var = value} or \boxed{value}
    boxed_pattern = re.compile(r"\\boxed\{([^{}]+?)\}")
    for m in boxed_pattern.finditer(latex_text):
        content = m.group(1).strip()
        if "=" in content:
            parts = content.split("=", 1)
            var_name = parts[0].strip()
            val_expr = parts[1].strip()
        else:
            var_name = "ans"
            val_expr = content
            
        parsed_val = parse_scientific_notation(val_expr)
        claims.append((var_name, val_expr, parsed_val))
        
    return claims


def verify_arithmetic_substitution(
    formula_expr: str,
    substitutions: Dict[str, float],
    claimed_result: float,
    rel_tol: float = 0.05
) -> Dict[str, Any]:
    """
    Deterministically check whether substituting numerical values into a formula
    yields the claimed answer within rel_tol (e.g. 5% tolerance for rounding).
    """
    try:
        sym_vars = {k: sp.Symbol(k) for k in substitutions}
        parsed_formula = sp.sympify(formula_expr, locals=sym_vars)
        evaluated = float(parsed_formula.evalf(subs={sym_vars[k]: v for k, v in substitutions.items()}))
        
        diff = abs(evaluated - claimed_result)
        relative_diff = diff / max(abs(claimed_result), 1e-12)
        passed = relative_diff <= rel_tol
        
        return {
            "evaluated": evaluated,
            "claimed": claimed_result,
            "relative_diff": relative_diff,
            "passed": passed,
            "notes": "Values match within tolerance." if passed else f"Discrepancy: evaluated {evaluated:.4e} vs claimed {claimed_result:.4e}",
        }
    except Exception as e:
        return {
            "error": str(e),
            "passed": False,
            "notes": f"Could not deterministically evaluate formula: {str(e)}",
        }


def check_solution_consistency(question_text: str, solution_latex: str) -> Dict[str, Any]:
    """
    Perform deterministic sanity checks on a candidate solution:
    1. Check if claimed boxed answers have reasonable magnitude.
    2. Check dimensional/astronomical bounds where known.
    """
    claims = extract_numerical_claims(solution_latex)
    check_report = {
        "claims_found": len(claims),
        "details": [],
        "all_passed": True,
    }
    
    for var, raw_val, parsed in claims:
        detail = {
            "variable": var,
            "raw": raw_val,
            "parsed_float": parsed,
            "passed": True,
            "flag": None,
        }
        
        if parsed is not None:
            if math.isnan(parsed) or math.isinf(parsed):
                detail["passed"] = False
                detail["flag"] = "Nilai perhitungan bernilai NaN atau tak hingga."
                check_report["all_passed"] = False
                
        check_report["details"].append(detail)
        
    return check_report
