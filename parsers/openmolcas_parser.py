"""OpenMolcas input and output parser."""

import os
import re

from .common import DEFAULT_SLURM_LABELS, format_standard_body, float_fmt, yes_no

TAG_FIELDS = ['job_type', 'basis']
FILE_FIELDS = ['coord_file', 'orbital_file']

JOB_LABELS = [
    ('Project', 'project_label'),
    ('Code', 'code'),
    ('Input File', 'input_file'),
    ('Title', 'title'),
    ('Job Type', 'job_type'),
    ('Coordinate File', 'coord_file'),
    ('Basis', 'basis'),
    ('Group', 'group'),
    ('Orbital File', 'orbital_file'),
    ('Spin', 'spin'),
    ('NACTEL', 'nactel'),
    ('Inactive', 'inactive'),
    ('RAS1', 'ras1'),
    ('RAS2', 'ras2'),
    ('CIROOT', 'ciroot'),
    ('MaxOrb', 'maxorb'),
    ('MOLCAS_MEM', 'molcas_mem_mb'),
]

RESULT_LABELS = [
    ('Terminated Normally', 'terminated_normally', yes_no),
    ('OpenMolcas Version', 'openmolcas_version'),
    ('pymolcas Version', 'pymolcas_version'),
    ('Final Energy (a.u.)', 'final_energy_au', float_fmt(8)),
    ('Root Energies', 'root_energies'),
    ('Convergence Iterations', 'convergence_iterations'),
    ('Wall Time (s)', 'wall_time_s', float_fmt(2)),
    ('User Time (s)', 'user_time_s', float_fmt(2)),
    ('System Time (s)', 'system_time_s', float_fmt(2)),
]


def parse_openmolcas_input(inp_path: str) -> dict:
    metadata = {
        'code': 'OpenMolcas',
        'input_file': os.path.basename(inp_path),
        'title': None,
        'job_type': 'RASSCF',
        'coord_file': None,
        'basis': None,
        'group': None,
        'orbital_file': None,
        'spin': None,
        'nactel': None,
        'inactive': None,
        'ras1': None,
        'ras2': None,
        'ciroot': None,
        'maxorb': None,
        'molcas_mem_mb': None,
        'parse_errors': [],
    }
    try:
        with open(inp_path, 'r', errors='replace') as handle:
            raw = handle.read()
    except Exception as exc:
        metadata['parse_errors'].append(f'Could not read input file: {exc}')
        return metadata

    def find(pattern, flags=re.IGNORECASE | re.MULTILINE):
        match = re.search(pattern, raw, flags)
        return match.group(1).strip() if match else None

    metadata['molcas_mem_mb'] = find(r'>>>\s*export\s+MOLCAS_MEM\s*=\s*(\S+)')
    metadata['title'] = find(r'Title\s*=\s*(.+)')
    metadata['coord_file'] = find(r'Coord\s*=\s*(\S+)')
    metadata['basis'] = find(r'Basis\s*=\s*(\S+)')
    metadata['group'] = find(r'Group\s*=\s*(\S+)')
    metadata['orbital_file'] = find(r'Fileorb\s*=\s*(\S+)')
    metadata['spin'] = find(r'SPIN\s*=\s*(\S+)')
    metadata['nactel'] = find(r'NACTEL\s*=\s*([^\n]+)')
    metadata['inactive'] = find(r'INACTIVE\s*=\s*(\S+)')
    metadata['ras1'] = find(r'RAS1\s*=\s*(\S+)')
    metadata['ras2'] = find(r'RAS2\s*=\s*(\S+)')
    metadata['ciroot'] = find(r'CIROOT\s*=\s*([^\n]+)')
    metadata['maxorb'] = find(r'MAXOrb\s*=\s*(\S+)')
    return metadata


def parse_openmolcas_output(out_path: str) -> dict:
    results = {
        'terminated_normally': False,
        'openmolcas_version': None,
        'pymolcas_version': None,
        'final_energy_au': None,
        'root_energies': {},
        'convergence_iterations': None,
        'wall_time_s': None,
        'user_time_s': None,
        'system_time_s': None,
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

    version = re.search(r'OPENMOLCAS.*?version:\s*(\S+)', content, re.IGNORECASE | re.DOTALL)
    if version:
        results['openmolcas_version'] = version.group(1)
    pyver = re.search(r'pymolcas version\s+(\S+)', content, re.IGNORECASE)
    if pyver:
        results['pymolcas_version'] = pyver.group(1)
    for root, energy in re.findall(r'RASSCF root number\s+(\d+) Total energy:\s*([+-]?\d+\.\d+)', content):
        results['root_energies'][f'root_{root}'] = float(energy)
    if results['root_energies']:
        first_key = sorted(results['root_energies'].keys())[0]
        results['final_energy_au'] = results['root_energies'][first_key]
    convergences = re.findall(r'Convergence after\s+(\d+)\s+(?:Macro )?Iterations?', content, re.IGNORECASE)
    if convergences:
        results['convergence_iterations'] = int(convergences[-1])
    timing = re.search(r'Timing:\s*Wall=([\d.]+)\s+User=([\d.]+)\s+System=([\d.]+)', content)
    if timing:
        results['wall_time_s'] = float(timing.group(1))
        results['user_time_s'] = float(timing.group(2))
        results['system_time_s'] = float(timing.group(3))
    results['terminated_normally'] = 'Happy landing!' in content
    warnings = []
    for line in re.findall(r'^.*WARNING:.*$', content, re.MULTILINE):
        warnings.append(line.strip())
    if 'floating-point exceptions' in content:
        warnings.append('Floating-point exceptions were reported in the output.')
    results['warnings'] = warnings[:10]
    return results


def format_elabftw_body_openmolcas(input_meta: dict, slurm_meta: dict, output_results=None) -> str:
    return format_standard_body(
        input_meta,
        slurm_meta,
        output_results,
        job_labels=JOB_LABELS,
        slurm_labels=DEFAULT_SLURM_LABELS,
        result_labels=RESULT_LABELS,
    )
