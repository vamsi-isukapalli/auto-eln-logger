"""Detect workflow structure from Slurm submit scripts.

This module is intentionally heuristic. It does not try to fully parse Bash.
Instead it recognizes the common patterns used in this group:
- single-run jobs
- while-read loop workflows
- Slurm array workflows
- sequential multi-step jobs (for example Amber stage chains)
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Dict, List, Optional

from .code_detector import detect_code


def _clean_value(value: str) -> str:
    value = value.strip().strip('"\'')
    value = re.sub(r'\${[^}]+}', '', value)
    value = re.sub(r'\$[A-Za-z_][A-Za-z0-9_]*', '', value)
    value = value.strip()
    return value


def _parse_sbatch_directives(script_content: str) -> dict:
    directives = {}
    for line in script_content.splitlines():
        stripped = line.strip()
        if not stripped.startswith('#SBATCH'):
            continue
        payload = stripped.split(None, 1)[1] if ' ' in stripped else ''
        payload = payload.split('#', 1)[0].strip()
        if not payload:
            continue
        if payload.startswith('--'):
            token = payload[2:]
            if '=' in token:
                key, value = token.split('=', 1)
            elif ' ' in token:
                key, value = token.split(None, 1)
            else:
                key, value = token, 'true'
            directives[key.replace('-', '_')] = value.strip()
            continue
        parts = payload.split(None, 1)
        flag = parts[0]
        value = parts[1].strip() if len(parts) > 1 else 'true'
        if flag in ('-o',):
            directives['output'] = value
        elif flag in ('-e',):
            directives['error'] = value
        elif flag in ('-J',):
            directives['job_name'] = value
        elif flag in ('-p',):
            directives['partition'] = value
        elif flag in ('-n',):
            directives['ntasks'] = value
        elif flag in ('-N',):
            directives['nodes'] = value
        elif flag in ('-t',):
            directives['time'] = value
    return directives


def _guess_main_output(submit_dir: Path, directives: dict) -> Optional[str]:
    output = (directives.get('output') or '').strip()
    if not output:
        return None
    # Keep Slurm substitutions. bmdeln_epilog will expand them later.
    path = Path(output)
    if not path.is_absolute():
        path = submit_dir / path
    return str(path)


def _read_step_source(path: Path, limit: int = 2000) -> List[str]:
    if not path.exists() or not path.is_file():
        return []
    steps = []
    try:
        with open(path, 'r', errors='replace') as handle:
            for idx, line in enumerate(handle):
                if idx >= limit:
                    break
                item = line.strip()
                if item:
                    steps.append(item)
    except Exception:
        return []
    return steps


def _extract_loop_source(script_content: str) -> tuple[Optional[str], Optional[str]]:
    # while read -r stepname; do ... done < "$WorkDir/stepnames.txt"
    m = re.search(r'while\s+read(?:\s+-r)?\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*do[\s\S]*?done\s*<\s*["\']?([^"\'\n]+)["\']?', script_content, re.IGNORECASE)
    if m:
        return m.group(1), m.group(2)
    # for step in $(cat file)
    m = re.search(r'for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+\$\(\s*cat\s+([^\)]+)\)', script_content)
    if m:
        return m.group(1), m.group(2).strip().strip('"\'')
    return None, None


def _extract_array_source(script_content: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    # stepname=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "${WorkDir}/stepnames.txt")
    m = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\$\(\s*sed\s+-n\s+["\']?\$\{?SLURM_ARRAY_TASK_ID\}?p["\']?\s+["\']([^"\']+)["\']\s*\)', script_content)
    if m:
        return m.group(1), m.group(2), 'sed'
    m = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\$\(\s*awk\s+[^\)]*SLURM_ARRAY_TASK_ID[^\)]*\s+["\']([^"\']+)["\']\s*\)', script_content)
    if m:
        return m.group(1), m.group(2), 'awk'
    return None, None, None


def _infer_primary_inputs(script_content: str, code: str) -> List[str]:
    candidates: List[str] = []
    try:
        lines = [ln.strip() for ln in script_content.splitlines() if ln.strip() and not ln.strip().startswith('#')]
    except Exception:
        lines = []
    for line in lines:
        if code == 'CP2K':
            for pat in [r'\bcp2k\S*\s+-i\s+(\S+)', r'\bcp2k\S*\s+(\S+\.inp)\b']:
                for m in re.findall(pat, line, re.IGNORECASE):
                    candidates.append(_clean_value(m))
        elif code == 'ORCA':
            for pat in [r'\borca\b\s+(\S+\.(?:inp|orc))\b']:
                for m in re.findall(pat, line, re.IGNORECASE):
                    candidates.append(_clean_value(m))
        elif code == 'Molpro':
            for pat in [r'\bmolpro\b(?:\s+\S+)*\s+(\S+\.(?:inp|com))\b']:
                for m in re.findall(pat, line, re.IGNORECASE):
                    candidates.append(_clean_value(m))
        elif code == 'Amber':
            for pat in [r'\s-i\s+(\S+)', r'\s-c\s+(\S+)', r'\s-p\s+(\S+)']:
                for m in re.findall(pat, line):
                    candidates.append(_clean_value(m))
        elif code == 'OpenMolcas':
            for pat in [r'\bpymolcas(?:_\d+)?\b\s+(\S+)']:
                for m in re.findall(pat, line, re.IGNORECASE):
                    item = _clean_value(m)
                    if item and not item.endswith('.out'):
                        candidates.append(item)
    seen = []
    for item in candidates:
        if not item or '$' in item:
            continue
        if item.startswith('.') or item in ('.inp', '.out', '.log'):
            continue
        if item not in seen:
            seen.append(item)
    return seen


def _summarize_steps(step_names: List[str], limit: int = 50) -> List[dict]:
    steps = []
    for idx, name in enumerate(step_names[:limit], start=1):
        steps.append({
            'step_index': idx - 1,
            'step_id': f'step{idx:03d}',
            'display_name': name,
            'status': 'PENDING',
        })
    return steps


def detect_workflow(script_content: str, submit_dir: str | Path) -> dict:
    submit_dir = Path(submit_dir)
    directives = _parse_sbatch_directives(script_content)
    code = detect_code(script_content, '')
    main_output = _guess_main_output(submit_dir, directives)
    workflow = {
        'workflow_type': 'single',
        'main_output': main_output,
        'step_source_file': None,
        'step_source_entries': [],
        'step_count': None,
        'array_spec': directives.get('array'),
        'primary_inputs': _infer_primary_inputs(script_content, code),
        'code_hint': code,
        'description': 'Single-run job',
        'steps': [],
    }

    if directives.get('array'):
        var_name, source_file, _mode = _extract_array_source(script_content)
        workflow['workflow_type'] = 'array'
        workflow['description'] = 'Slurm array workflow'
        workflow['step_variable'] = var_name
        if source_file:
            workflow['step_source_file'] = source_file
            source_path = submit_dir / Path(_clean_value(source_file)).name if '$' in source_file else Path(source_file)
            if not source_path.is_absolute():
                source_path = submit_dir / source_path
            entries = _read_step_source(source_path)
            workflow['step_source_entries'] = entries
            workflow['step_count'] = len(entries) if entries else None
            workflow['steps'] = _summarize_steps(entries)
        return workflow

    loop_var, loop_source = _extract_loop_source(script_content)
    if loop_var:
        workflow['workflow_type'] = 'loop'
        workflow['description'] = 'Loop workflow'
        workflow['step_variable'] = loop_var
        workflow['step_source_file'] = loop_source
        source_path = Path(_clean_value(loop_source or ''))
        if source_path and not source_path.is_absolute():
            source_path = submit_dir / source_path.name if '$' in str(source_path) else submit_dir / source_path
        entries = _read_step_source(source_path) if str(source_path) not in ('', '.') else []
        workflow['step_source_entries'] = entries
        workflow['step_count'] = len(entries) if entries else None
        workflow['steps'] = _summarize_steps(entries)
        return workflow

    return workflow
