"""CP2K input and output parser."""

import os
import re

from .common import DEFAULT_SLURM_LABELS, format_standard_body, float_fmt, yes_no, list_to_string

TAG_FIELDS = ['run_type', 'functional']
FILE_FIELDS = ['coord_file']

JOB_LABELS = [
    ('Project', 'project_label'),
    ('Code', 'code'),
    ('Input File', 'input_file'),
    ('Project Name', 'project_name'),
    ('Run Type', 'run_type'),
    ('Method', 'method'),
    ('Functional', 'functional'),
    ('HFX Fraction', 'hfx_fraction'),
    ('VdW Correction', 'vdw_correction'),
    ('ADMM', 'admm', yes_no),
    ('Basis Set (H)', 'basis_set_H'),
    ('Basis Set (O)', 'basis_set_O'),
    ('Basis Set (Other)', 'basis_set_other', list_to_string),
    ('Cutoff (Ry)', 'cutoff'),
    ('Rel. Cutoff', 'rel_cutoff'),
    ('Charge', 'charge'),
    ('Multiplicity', 'multiplicity'),
    ('Cell ABC', 'cell_abc'),
    ('Coordinate File', 'coord_file'),
    ('SCF Guess', 'scf_guess'),
    ('MD Ensemble', 'md_ensemble'),
    ('MD Steps', 'md_steps'),
    ('MD Timestep (fs)', 'md_timestep_fs'),
    ('MD Temperature (K)', 'md_temperature_K'),
    ('Geo Opt Optimizer', 'geo_opt_optimizer'),
    ('Geo Opt Max Iter', 'geo_opt_max_iter'),
]

RESULT_LABELS = [
    ('Terminated Normally', 'terminated_normally', yes_no),
    ('Final Energy (a.u.)', 'final_energy_au', float_fmt(10)),
    ('SCF Converged', 'scf_converged', yes_no),
    ('Total Time (s)', 'total_time_s'),
    ('CP2K Version', 'cp2k_version'),
    ('MD Steps Completed', 'md_steps_completed'),
    ('Warnings', 'warnings', list_to_string),
]


def parse_cp2k_input(inp_path: str) -> dict:
    metadata = {
        'code': 'CP2K',
        'input_file': os.path.basename(inp_path),
        'project_name': None,
        'run_type': None,
        'method': 'QS/DFT',
        'functional': None,
        'basis_set_H': None,
        'basis_set_O': None,
        'basis_set_other': [],
        'cutoff': None,
        'rel_cutoff': None,
        'charge': None,
        'multiplicity': None,
        'cell_abc': None,
        'coord_file': None,
        'vdw_correction': None,
        'hfx_fraction': None,
        'scf_guess': None,
        'md_ensemble': None,
        'md_steps': None,
        'md_timestep_fs': None,
        'md_temperature_K': None,
        'geo_opt_optimizer': None,
        'geo_opt_max_iter': None,
        'admm': False,
        'print_level': None,
        'parse_errors': [],
    }
    try:
        with open(inp_path, 'r', errors='replace') as handle:
            raw = handle.read()
    except Exception as exc:
        metadata['parse_errors'].append(f'Could not read input file: {exc}')
        return metadata

    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in ('#', '!'):
            continue
        cleaned = re.sub(r'\s+[#!].*$', '', stripped).strip()
        if cleaned:
            lines.append(cleaned)
    content = '\n'.join(lines)

    def find(pattern, text=content, flags=re.IGNORECASE | re.MULTILINE):
        match = re.search(pattern, text, flags)
        return match.group(1).strip() if match else None

    metadata['project_name'] = find(r'(?:^|\n)\s*PROJECT\s+(\S+)')
    metadata['run_type'] = find(r'(?:^|\n)\s*RUN_TYPE\s+(\S+)')
    metadata['print_level'] = find(r'(?:^|\n)\s*PRINT_LEVEL\s+(\S+)')
    metadata['cutoff'] = find(r'(?:^|\n)\s*CUTOFF\s+([\d.]+)')
    metadata['rel_cutoff'] = find(r'(?:^|\n)\s*REL_CUTOFF\s+([\d.]+)')
    metadata['scf_guess'] = find(r'(?:^|\n)\s*SCF_GUESS\s+(\S+)')
    metadata['charge'] = find(r'(?:^|\n)\s*CHARGE\s+([+-]?\d+)')
    metadata['multiplicity'] = find(r'(?:^|\n)\s*MULTIPLICITY\s+(\d+)')

    func_block = re.search(r'&XC_FUNCTIONAL(.*?)&END XC_FUNCTIONAL', content, re.IGNORECASE | re.DOTALL)
    if func_block:
        funcs = re.findall(r'&(\w+)\s*\n', func_block.group(1))
        funcs = [f for f in funcs if f.upper() != 'END']
        if funcs:
            metadata['functional'] = ', '.join(funcs)

    metadata['hfx_fraction'] = find(r'FRACTION\s+([\d.]+)')
    metadata['vdw_correction'] = find(r'TYPE\s+(DFTD\w+)')
    metadata['admm'] = bool(re.search(r'&AUXILIARY_DENSITY_MATRIX_METHOD', content, re.IGNORECASE))
    cell = find(r'ABC\s+([\d.\s]+)')
    if cell:
        metadata['cell_abc'] = cell.strip()
    metadata['coord_file'] = find(r'COORD_FILE_NAME\s+(\S+)')

    kind_blocks = re.findall(r'&KIND\s+(\w+)(.*?)&END KIND', content, re.IGNORECASE | re.DOTALL)
    kind_basis = {}
    for kind_name, kind_body in kind_blocks:
        basis_matches = re.findall(r'BASIS_SET\s+(?!AUX_FIT)(\S+)', kind_body, re.IGNORECASE)
        if basis_matches:
            kind_basis[kind_name.upper()] = basis_matches[0]
    metadata['basis_set_H'] = kind_basis.get('H')
    metadata['basis_set_O'] = kind_basis.get('O')
    metadata['basis_set_other'] = [f'{k}:{v}' for k, v in kind_basis.items() if k not in ('H', 'O')]

    metadata['md_ensemble'] = find(r'(?:^|\n)\s*ENSEMBLE\s+(\S+)')
    metadata['md_steps'] = find(r'(?:^|\n)\s*STEPS\s+(\d+)')
    metadata['md_timestep_fs'] = find(r'TIMESTEP\s+\[fs\]\s+([\d.]+)')
    metadata['md_temperature_K'] = find(r'TEMPERATURE\s+\[K\]\s+([\d.]+)')
    metadata['geo_opt_optimizer'] = find(r'(?:^|\n)\s*OPTIMIZER\s+(\S+)')
    metadata['geo_opt_max_iter'] = find(r'(?:^|\n)\s*MAX_ITER\s+(\d+)')
    return metadata


def _parse_timing_seconds(content: str):
    patterns = [
        r'Total program time.*?:\s*([\d.]+)',
        r'^\s*CP2K\s+([\d.]+)\s*$',
        r'^\s*CP2K\s+[\d.]+\s+[\d.]+\s+([\d.]+)\s*$',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
        if matches:
            try:
                return float(matches[-1])
            except Exception:
                continue
    return None


def parse_cp2k_output(log_path: str) -> dict:
    results = {
        'terminated_normally': False,
        'final_energy_au': None,
        'scf_converged': None,
        'total_time_s': None,
        'cp2k_version': None,
        'md_steps_completed': None,
        'warnings': [],
        'parse_errors': [],
    }
    if not os.path.exists(log_path):
        results['parse_errors'].append(f'Output file not found: {log_path}')
        return results
    try:
        with open(log_path, 'r', errors='replace') as handle:
            content = handle.read()
    except Exception as exc:
        results['parse_errors'].append(f'Could not read output file: {exc}')
        return results

    if re.search(r'CP2K\s+CONTROLLER\s+WORKER\s+STEP\s+DONE', content) or re.search(r'T I M I N G', content) or re.search(r'PROGRAM ENDED AT', content):
        results['terminated_normally'] = True
    version = re.search(r'CP2K\s+version\s+([\d.]+)', content, re.IGNORECASE)
    if version:
        results['cp2k_version'] = version.group(1)

    energy_patterns = [
        r'ENERGY\|\s+Total FORCE_EVAL.*?[:=]\s*([+-]?\d+\.\d+)',
        r'ENERGY\|.*?energy\s*(?:\[a\.u\.\])?\s*[:=]\s*([+-]?\d+\.\d+)',
        r'Total energy\s*[:=]\s*([+-]?\d+\.\d+)',
    ]
    for pattern in energy_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            try:
                results['final_energy_au'] = float(matches[-1])
                break
            except Exception:
                pass

    if re.search(r'SCF run NOT converged', content, re.IGNORECASE):
        results['scf_converged'] = False
    elif re.search(r'SCF run converged in\s+\d+\s+iterations', content, re.IGNORECASE):
        results['scf_converged'] = True
    elif re.search(r'outer SCF loop converged', content, re.IGNORECASE):
        results['scf_converged'] = True

    results['total_time_s'] = _parse_timing_seconds(content)

    md_step_patterns = [
        r'MD\|\s+Step number\s+(\d+)',
        r'STEP NUMBER\s+(\d+)',
        r'Information at step\s*=\s*(\d+)',
    ]
    md_steps = []
    for pattern in md_step_patterns:
        md_steps.extend(re.findall(pattern, content, re.IGNORECASE))
    if md_steps:
        try:
            results['md_steps_completed'] = int(md_steps[-1])
        except Exception:
            pass

    ignore_warn = ['Kohn Sham matrix', 'hfx_energy_potential', 'thermostat_methods']
    warnings = re.findall(r'\*\*\*\s+WARNING.*', content)
    results['warnings'] = [w for w in warnings if not any(needle in w for needle in ignore_warn)][:10]
    return results


def format_elabftw_body(input_meta: dict, slurm_meta: dict, output_results=None) -> str:
    return format_standard_body(
        input_meta,
        slurm_meta,
        output_results,
        job_labels=JOB_LABELS,
        slurm_labels=DEFAULT_SLURM_LABELS,
        result_labels=RESULT_LABELS,
    )
