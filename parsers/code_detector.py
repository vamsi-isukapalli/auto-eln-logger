"""
Detect which QC code a job script is using.
Used to warn about non-CP2K jobs in eLabFTW.
"""

import re

# Map of known codes to patterns found in job scripts
CODE_SIGNATURES = {
    "ORCA":     [r"orca\s", r"/Orca/", r"\.orca\b", r"orca\.inp"],
    "Amber":    [r"sander", r"pmemd", r"amber", r"\.prmtop", r"\.inpcrd"],
    "Molpro":   [r"molpro", r"\.com\b.*molpro"],
    "NWChem":   [r"nwchem"],
    "OpenMolcas":[r"pymolcas", r"molcas"],
    "xTB":      [r"\bxtb\b"],
    "CP2K":     [r"cp2k", r"cp2k\.popt", r"cp2k\.psmp"],
}

def detect_code(job_script_content: str) -> str:
    """
    Detect the QC code used in a Slurm job script.
    Returns the code name string, or 'Unknown'.
    """
    text = job_script_content.lower()
    for code, patterns in CODE_SIGNATURES.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return code
    return "Unknown"
