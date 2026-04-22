"""Amber input, output, and multi-stage submit-script parser."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import List

from .common import DEFAULT_SLURM_LABELS, format_standard_body, float_fmt, html_escape, yes_no

TAG_FIELDS = ['job_type']
FILE_FIELDS = []

JOB_LABELS = [
    ('Project', 'project_label'),
    ('Code', 'code'),
    ('Input File', 'input_file'),
    ('Title', 'title'),
    ('Job Type', 'job_type'),
    ('Restart', 'irest'),
    ('Minimization', 'imin'),
    ('Steps (nstlim)', 'nstlim'),
    ('Timestep (ps)', 'dt'),
    ('Print Every', 'ntpr'),
    ('Write Traj Every', 'ntwx'),
    ('Write Restart Every', 'ntwr'),
    ('Cutoff', 'cut'),
    ('Box Control (ntb)', 'ntb'),
    ('Pressure Control (ntp)', 'ntp'),
    ('Thermostat (ntt)', 'ntt'),
    ('Initial Temp (K)', 'tempi'),
    ('Target Temp (K)', 'temp0'),
    ('Target Pressure', 'pres0'),
    ('Barostat', 'barostat'),
    ('Gamma_ln', 'gamma_ln'),
    ('Restraints', 'ntr'),
    ('Restraint Weight', 'restraint_wt'),
    ('Restraint Mask', 'restraintmask'),
]

RESULT_LABELS = [
    ('Terminated Normally', 'terminated_normally', yes_no),
    ('Amber Version', 'amber_version'),
    ('MPI Tasks', 'mpi_tasks'),
    ('Final Step', 'final_nstep'),
    ('Final Time (ps)', 'final_time_ps'),
    ('Final Temp (K)', 'final_temp_k', float_fmt(2)),
    ('Final Pressure', 'final_pressure', float_fmt(1)),
    ('Final Etot', 'final_etot', float_fmt(4)),
    ('Final Density', 'final_density', float_fmt(4)),
    ('Average Temp (K)', 'avg_temp_k', float_fmt(2)),
    ('Average Pressure', 'avg_pressure', float_fmt(1)),
    ('Average Etot', 'avg_etot', float_fmt(4)),
    ('Average Density', 'avg_density', float_fmt(4)),
    ('ns/day', 'ns_per_day', float_fmt(2)),
    ('Elapsed (s)', 'elapsed_s', float_fmt(2)),
    ('Master Total Wall (s)', 'master_total_wall_s', float_fmt(2)),
]

FIELD_PATTERNS = {
    'imin': r'\bimin\s*=\s*([^,\n/]+)',
    'irest': r'\birest\s*=\s*([^,\n/]+)',
    'nstlim': r'\bnstlim\s*=\s*([^,\n/]+)',
    'dt': r'\bdt\s*=\s*([^,\n/]+)',
    'ntpr': r'\bntpr\s*=\s*([^,\n/]+)',
    'ntwx': r'\bntwx\s*=\s*([^,\n/]+)',
    'ntwr': r'\bntwr\s*=\s*([^,\n/]+)',
    'cut': r'\bcut\s*=\s*([^,\n/]+)',
    'ntb': r'\bntb\s*=\s*([^,\n/]+)',
    'ntp': r'\bntp\s*=\s*([^,\n/]+)',
    'ntt': r'\bntt\s*=\s*([^,\n/]+)',
    'tempi': r'\btempi\s*=\s*([^,\n/]+)',
    'temp0': r'\btemp0\s*=\s*([^,\n/]+)',
    'pres0': r'\bpres0\s*=\s*([^,\n/]+)',
    'barostat': r'\bbarostat\s*=\s*([^,\n/]+)',
    'gamma_ln': r'\bgamma_ln\s*=\s*([^,\n/]+)',
    'ig': r'\big\s*=\s*([^,\n/]+)',
    'ntr': r'\bntr\s*=\s*([^,\n/]+)',
    'restraint_wt': r'\brestraint_wt\s*=\s*([^,\n/]+)',
    'restraintmask': r"\brestraintmask\s*=\s*'([^']+)'",
}


def parse_amber_input(inp_path: str) -> dict:
    metadata = {'code': 'Amber', 'input_file': os.path.basename(inp_path), 'title': None, 'job_type': None, 'parse_errors': []}
    metadata.update({key: None for key in FIELD_PATTERNS})
    try:
        with open(inp_path, 'r', errors='replace') as handle:
            raw = handle.read()
    except Exception as exc:
        metadata['parse_errors'].append(f'Could not read input file: {exc}')
        return metadata
    nonempty = [line.rstrip() for line in raw.splitlines() if line.strip()]
    if nonempty:
        metadata['title'] = nonempty[0].strip()
    for key, pattern in FIELD_PATTERNS.items():
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            metadata[key] = match.group(1).strip().rstrip(',')
    if str(metadata.get('imin')) == '1':
        metadata['job_type'] = 'MINIMIZATION'
    elif str(metadata.get('irest')) == '1':
        metadata['job_type'] = 'PRODUCTION_MD'
    else:
        metadata['job_type'] = 'MD'
    return metadata



def _extract_state_block(content: str, anchor: str) -> dict:
    pattern = anchor + r'.*?NSTEP\s*=\s*(\d+)\s+TIME\(PS\)\s*=\s*([\d.]+)\s+TEMP\(K\)\s*=\s*([+-]?[\d.]+)\s+PRESS\s*=\s*([+-]?[\d.]+).*?Etot\s*=\s*([+-]?[\d.]+).*?Density\s*=\s*([+-]?[\d.]+)'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return {}
    return {
        'nstep': int(match.group(1)),
        'time_ps': float(match.group(2)),
        'temp_k': float(match.group(3)),
        'pressure': float(match.group(4)),
        'etot': float(match.group(5)),
        'density': float(match.group(6)),
    }



def parse_amber_output(out_path: str) -> dict:
    results = {
        'terminated_normally': False,
        'amber_version': None,
        'mpi_tasks': None,
        'final_nstep': None,
        'final_time_ps': None,
        'final_temp_k': None,
        'final_pressure': None,
        'final_etot': None,
        'final_density': None,
        'avg_temp_k': None,
        'avg_pressure': None,
        'avg_etot': None,
        'avg_density': None,
        'ns_per_day': None,
        'elapsed_s': None,
        'master_total_wall_s': None,
        'warnings': [],
        'parse_errors': [],
    }
    if not os.path.exists(out_path):
        results['parse_errors'].append(f'Output file not found: {out_path}')
        return results
    try:
        with open(out_path, 'r', errors='replace') as handle:
            content = handle.read()
    except Exception as exc:
        results['parse_errors'].append(f'Could not read output file: {exc}')
        return results

    version = re.search(r'Amber\s+(\d+)\s+PMEMD\s+(\d+)', content)
    if version:
        results['amber_version'] = f"Amber {version.group(1)} PMEMD {version.group(2)}"
    mpi = re.search(r'Running AMBER/MPI version on\s+(\d+)\s+MPI task', content)
    if mpi:
        results['mpi_tasks'] = int(mpi.group(1))
    instantaneous_section = re.split(r'A V E R A G E S\s+O V E R', content, maxsplit=1)[0]
    final_blocks = list(re.finditer(r'NSTEP\s*=\s*(\d+)\s+TIME\(PS\)\s*=\s*([\d.]+)\s+TEMP\(K\)\s*=\s*([+-]?[\d.]+)\s+PRESS\s*=\s*([+-]?[\d.]+).*?Etot\s*=\s*([+-]?[\d.]+).*?Density\s*=\s*([+-]?[\d.]+)', instantaneous_section, re.DOTALL))
    if final_blocks:
        last = final_blocks[-1]
        results['final_nstep'] = int(last.group(1))
        results['final_time_ps'] = float(last.group(2))
        results['final_temp_k'] = float(last.group(3))
        results['final_pressure'] = float(last.group(4))
        results['final_etot'] = float(last.group(5))
        results['final_density'] = float(last.group(6))
    averages = _extract_state_block(content, r'A V E R A G E S\s+O V E R\s+\d+\s+S T E P S\s+')
    if averages:
        results['avg_temp_k'] = averages['temp_k']
        results['avg_pressure'] = averages['pressure']
        results['avg_etot'] = averages['etot']
        results['avg_density'] = averages['density']
    perf = re.search(r'Average timings for all steps:\s*\|\s*Elapsed\(s\)\s*=\s*([\d.]+).*?ns/day\s*=\s*([\d.]+)', content, re.DOTALL)
    if perf:
        results['elapsed_s'] = float(perf.group(1))
        results['ns_per_day'] = float(perf.group(2))
    wall = re.search(r'Master Total wall time:\s*(\d+)\s+seconds', content)
    if wall:
        results['master_total_wall_s'] = float(wall.group(1))
    results['terminated_normally'] = 'Final Performance Info' in content and 'Master Total wall time' in content
    warnings = []
    for line in re.findall(r'^.*WARNING:.*$', content, re.MULTILINE):
        warnings.append(line.strip())
    results['warnings'] = warnings[:10]
    return results



def parse_amber_submit_steps(script_content: str) -> List[dict]:
    steps: List[dict] = []
    for line in script_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if 'pmemd' not in stripped and 'sander' not in stripped:
            continue
        try:
            tokens = shlex.split(stripped)
        except Exception:
            tokens = stripped.split()
        executable = None
        for token in tokens:
            base = os.path.basename(token)
            if 'pmemd' in base.lower() or 'sander' in base.lower():
                executable = base
                break
        if not executable:
            continue
        step = {
            'step_index': len(steps),
            'step_id': f'run{len(steps) + 1:02d}',
            'display_name': None,
            'executable': executable,
            'mpi_ranks': None,
            'input_file': None,
            'restart_in': None,
            'topology_file': None,
            'output_file': None,
            'reference_file': None,
            'restart_out': None,
            'trajectory_file': None,
            'status': 'PENDING',
            'started_at': None,
            'finished_at': None,
            'exit_code': None,
            'line': stripped,
        }
        for idx, token in enumerate(tokens[:-1]):
            nxt = tokens[idx + 1]
            if token == '-np':
                step['mpi_ranks'] = nxt
            elif token == '-n':
                step['mpi_ranks'] = nxt
            elif token == '-i':
                step['input_file'] = nxt
            elif token == '-c':
                step['restart_in'] = nxt
            elif token == '-p':
                step['topology_file'] = nxt
            elif token == '-o':
                step['output_file'] = nxt
            elif token == '-ref':
                step['reference_file'] = nxt
            elif token == '-r':
                step['restart_out'] = nxt
            elif token == '-x':
                step['trajectory_file'] = nxt
        display = step['output_file'] or step['input_file'] or step['step_id']
        if display:
            step['display_name'] = Path(display).stem
        steps.append(step)
    return steps



def amber_steps_html(steps: List[dict]) -> str:
    if not steps:
        return ''
    rows = []
    for step in steps:
        rows.append(
            '<tr>'
            f"<td>{html_escape(step.get('step_id', '—'))}</td>"
            f"<td>{html_escape(step.get('display_name') or '—')}</td>"
            f"<td>{html_escape(step.get('input_file') or '—')}</td>"
            f"<td>{html_escape(step.get('restart_in') or '—')}</td>"
            f"<td>{html_escape(step.get('output_file') or '—')}</td>"
            f"<td>{html_escape(step.get('restart_out') or '—')}</td>"
            f"<td>{html_escape(step.get('trajectory_file') or '—')}</td>"
            f"<td>{html_escape(step.get('mpi_ranks') or '—')}</td>"
            f"<td>{html_escape(step.get('status') or 'PENDING')}</td>"
            '</tr>'
        )
    return (
        '<h2>Amber Simulation Steps</h2>'
        "<table border='1' cellpadding='5' cellspacing='0'>"
        '<tr><th>Step</th><th>Name</th><th>Input</th><th>Restart In</th><th>Output</th><th>Restart Out</th><th>Trajectory</th><th>MPI Ranks</th><th>Status</th></tr>'
        + ''.join(rows) +
        '</table>'
    )



def format_elabftw_body_amber(input_meta: dict, slurm_meta: dict, output_results=None) -> str:
    extra_sections = []
    steps = input_meta.get('amber_steps') or []
    if steps:
        extra_sections.append(amber_steps_html(steps))
    return format_standard_body(
        input_meta,
        slurm_meta,
        output_results,
        job_labels=JOB_LABELS,
        slurm_labels=DEFAULT_SLURM_LABELS,
        result_labels=RESULT_LABELS,
        extra_sections=extra_sections,
    )
