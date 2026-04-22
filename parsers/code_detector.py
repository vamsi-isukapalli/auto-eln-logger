"""Detect which QC/MD code a Slurm job script is using."""

from __future__ import annotations

import re
from pathlib import Path

CODE_SIGNATURES = {
    'OpenMolcas': [r'\bpymolcas(?:_\d+)?\b', r'\bopenmolcas\b', r'\bmolcas\b'],
    'ORCA': [r'\borca\b', r'orca_\d', r'\.orc\b', r'\.inp\b.*\borca\b'],
    'Amber': [r'\bpmemd\b', r'\bsander\b', r'\.parm7\b', r'\.prmtop\b', r'\.inpcrd\b', r'\bamber\b'],
    'Molpro': [r'\bmolpro\b', r'\.com\b'],
    'CP2K': [r'\bcp2k\b', r'cp2k\.popt', r'cp2k\.psmp'],
    'NWChem': [r'\bnwchem\b'],
    'xTB': [r'\bxtb\b'],
}

INPUT_HINTS = {
    '.orc': 'ORCA',
    '.mol': 'Molpro',
    '.com': 'Molpro',
}


def detect_code(job_script_content: str, input_filename: str = '') -> str:
    active_lines = []
    for line in job_script_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        active_lines.append(stripped)
    text = '\n'.join(active_lines).lower()
    for code, patterns in CODE_SIGNATURES.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return code
    suffix = Path(input_filename).suffix.lower()
    if suffix in INPUT_HINTS:
        return INPUT_HINTS[suffix]
    return 'Unknown'
