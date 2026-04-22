"""Molpro input and output parser."""

import os
import re

from .common import DEFAULT_SLURM_LABELS, format_standard_body, float_fmt, list_to_string, yes_no

TAG_FIELDS = ['job_type', 'methods', 'basis']
FILE_FIELDS = ['geometry_file']

JOB_LABELS = [
    ('Project', 'project_label'),
    ('Code', 'code'),
    ('Input File', 'input_file'),
    ('Title', 'title'),
    ('Job Type', 'job_type'),
    ('Methods', 'methods', list_to_string),
    ('Functional', 'functional'),
    ('Basis Set', 'basis'),
    ('Symmetry', 'symmetry'),
    ('Geometry File', 'geometry_file'),
    ('Geometry Type', 'geom_type'),
    ('Memory', 'memory_mw'),
    ('WF Electrons', 'wf_electrons'),
    ('WF Symmetry', 'wf_symmetry'),
    ('WF Spin', 'wf_spin'),
]

RESULT_LABELS = [
    ('Terminated Normally', 'terminated_normally', yes_no),
    ('Warnings', 'n_warnings'),
    ('Errors', 'n_errors'),
    ('Molpro Version', 'molpro_version'),
    ('Final Energy (a.u.)', 'final_energy_au', float_fmt(10)),
    ('Method Energies', 'method_energies'),
    ('HOMO (eV)', 'homo_ev', float_fmt(4)),
    ('LUMO (eV)', 'lumo_ev', float_fmt(4)),
    ('Gap (eV)', 'gap_ev', float_fmt(4)),
    ('Dipole', 'dipole'),
    ('Real Time (s)', 'real_time_s'),
    ('CPU Time (s)', 'cpu_time_s'),
    ('Disk Used (MB)', 'disk_used_mb'),
]


def parse_molpro_input(inp_path: str) -> dict:
    metadata = {
        'code': 'Molpro',
        'input_file': os.path.basename(inp_path),
        'title': None,
        'memory_mw': None,
        'basis': None,
        'symmetry': None,
        'geometry_file': None,
        'geom_type': None,
        'methods': [],
        'functional': None,
        'charge': None,
        'spin': None,
        'job_type': None,
        'wf_electrons': None,
        'wf_symmetry': None,
        'wf_spin': None,
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
        if not stripped or stripped.startswith('!'):
            continue
        cleaned = re.split(r'\s*!.*$', stripped)[0].strip()
        if cleaned:
            lines.append(cleaned)
    content = '\n'.join(lines)

    def find(pattern, text=content, flags=re.IGNORECASE | re.MULTILINE):
        match = re.search(pattern, text, flags)
        return match.group(1).strip() if match else None

    title = re.search(r'^\*\*\*,?\s*(.+)', raw, re.MULTILINE)
    if title:
        metadata['title'] = title.group(1).strip()
    memory = re.search(r'(?:^|\n)\s*memory\s*,\s*([\d.]+)\s*,\s*(\w+)', content, re.IGNORECASE | re.MULTILINE)
    if memory:
        metadata['memory_mw'] = f"{memory.group(1)} {memory.group(2).lower()}"
    basis_default = find(r'DEFAULT\s*=\s*(\S+)')
    if basis_default:
        metadata['basis'] = basis_default
    else:
        basis_block = re.search(r'BASIS\s*\{?(.*?)\}?END', content, re.IGNORECASE | re.DOTALL)
        if basis_block:
            metadata['basis'] = 'custom (see input)'
    symmetry = find(r'(?:^|\n)\s*symmetry\s*,\s*(\S+)')
    metadata['symmetry'] = symmetry.rstrip(';').rstrip(',') if symmetry else 'nosym'
    geometry_file = find(r'geometry\s*=\s*(\S+\.xyz)')
    if geometry_file:
        metadata['geometry_file'] = geometry_file
        metadata['geom_type'] = 'xyz'
    else:
        metadata['geom_type'] = find(r'geomtyp\s*=\s*(\S+)')

    method_patterns = {
        'DF-RKS/DFT': r'\{?\s*df-rks\s*,',
        'RKS/DFT': r'\{?\s*rks\s*,',
        'HF': r'\{?\s*hf\s*[;\}]',
        'CCSD(T)': r'\{?\s*ccsd\s*\(\s*t\s*\)',
        'CCSD': r'\{?\s*ccsd\s*[;\}]',
        'MP2': r'\{?\s*mp2\s*[;\}]',
        'CASSCF': r'\{?\s*casscf\s*[;\}]',
        'CASPT2': r'\{?\s*caspt2\s*[;\}]',
        'MRCI': r'\{?\s*mrci\s*[;\}]',
        'CI': r'\{?\s*ci\s*[;\}]',
    }
    methods = [name for name, pattern in method_patterns.items() if re.search(pattern, content, re.IGNORECASE)]
    if 'DF-RKS/DFT' in methods and 'RKS/DFT' in methods:
        methods.remove('RKS/DFT')
    metadata['methods'] = methods

    func = re.search(r'\{?\s*df-rks\s*,\s*(\S+)', content, re.IGNORECASE) or re.search(r'\{?\s*rks\s*,\s*(\S+)', content, re.IGNORECASE)
    if func:
        metadata['functional'] = func.group(1).strip()
    wf = re.search(r'wf\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', content, re.IGNORECASE)
    if wf:
        metadata['wf_electrons'] = wf.group(1)
        metadata['wf_symmetry'] = wf.group(2)
        metadata['wf_spin'] = wf.group(3)
    if re.search(r'^\s*optg', content, re.IGNORECASE | re.MULTILINE):
        metadata['job_type'] = 'GEOMETRY_OPT'
    elif re.search(r'^\s*freq', content, re.IGNORECASE | re.MULTILINE):
        metadata['job_type'] = 'FREQUENCIES'
    elif re.search(r'^\s*forces', content, re.IGNORECASE | re.MULTILINE):
        metadata['job_type'] = 'GRADIENT'
    else:
        metadata['job_type'] = 'ENERGY'
    return metadata


def parse_molpro_output(out_path: str) -> dict:
    results = {
        'terminated_normally': False,
        'n_warnings': 0,
        'n_errors': 0,
        'molpro_version': None,
        'final_energy_au': None,
        'method_energies': {},
        'homo_ev': None,
        'lumo_ev': None,
        'gap_ev': None,
        'dipole': None,
        'real_time_s': None,
        'cpu_time_s': None,
        'disk_used_mb': None,
        'parse_errors': [],
    }
    if not os.path.exists(out_path):
        results['parse_errors'].append(f'Output file not found: {out_path}')
        return results
    try:
        with open(out_path, 'r', errors='replace') as handle:
            content = handle.read()
    except Exception as exc:
        results['parse_errors'].append(f'Could not read output: {exc}')
        return results

    term = re.search(r'Molpro calculation terminated(?:\s+normally|\s+with\s+(\d+)\s+warning)?', content, re.IGNORECASE)
    if term:
        results['terminated_normally'] = True
        if term.group(1):
            results['n_warnings'] = int(term.group(1))
    version = re.search(r'Version\s+([\d.]+)', content)
    if version:
        results['molpro_version'] = version.group(1)
    energy_patterns = [
        (r'!RKS STATE.*?Energy\s+([+-]?\d+\.\d+)', 'DF-RKS'),
        (r'!HF STATE.*?Energy\s+([+-]?\d+\.\d+)', 'HF'),
        (r'!CCSD\(T\)\s+total energy\s+([+-]?\d+\.\d+)', 'CCSD(T)'),
        (r'!CCSD\s+total energy\s+([+-]?\d+\.\d+)', 'CCSD'),
        (r'!MP2\s+total energy\s+([+-]?\d+\.\d+)', 'MP2'),
        (r'!CASSCF\s+total energy\s+([+-]?\d+\.\d+)', 'CASSCF'),
        (r'!CASPT2\s+total energy\s+([+-]?\d+\.\d+)', 'CASPT2'),
        (r'DF-RKS/\S+\s+energy\s*=\s*([+-]?\d+\.\d+)', 'DF-RKS'),
    ]
    for pattern, method in energy_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            results['method_energies'][method] = float(matches[-1])
    for method in ['CCSD(T)', 'CASPT2', 'CCSD', 'MP2', 'CASSCF', 'DF-RKS', 'HF']:
        if method in results['method_energies']:
            results['final_energy_au'] = results['method_energies'][method]
            break
    homo = re.search(r'HOMO\s+\S+\s+[+-]?\d+\.\d+\s*=\s*([+-]?\d+\.\d+)eV', content)
    if homo:
        results['homo_ev'] = float(homo.group(1))
    lumo = re.search(r'LUMO\s+\S+\s+[+-]?\d+\.\d+\s*=\s*([+-]?\d+\.\d+)eV', content)
    if lumo:
        results['lumo_ev'] = float(lumo.group(1))
    gap = re.search(r'LUMO-HOMO\s+[+-]?\d+\.\d+\s*=\s*([+-]?\d+\.\d+)eV', content)
    if gap:
        results['gap_ev'] = float(gap.group(1))
    dipole = re.search(r'Dipole moment\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)', content)
    if dipole:
        results['dipole'] = f"{dipole.group(1)}, {dipole.group(2)}, {dipole.group(3)} a.u."
    real_times = re.findall(r'REAL TIME\s+\*\s+([\d.]+)\s+SEC', content)
    if real_times:
        results['real_time_s'] = float(real_times[-1])
    cpu_times = re.findall(r'CPU TIMES\s+\*\s+([\d.]+)', content)
    if cpu_times:
        results['cpu_time_s'] = float(cpu_times[-1])
    disk = re.search(r'DISK USED\s+\*\s+([\d.]+)\s+MB\s+\(local\)', content)
    if disk:
        results['disk_used_mb'] = float(disk.group(1))
    return results


def format_elabftw_body_molpro(input_meta: dict, slurm_meta: dict, output_results=None) -> str:
    return format_standard_body(
        input_meta,
        slurm_meta,
        output_results,
        job_labels=JOB_LABELS,
        slurm_labels=DEFAULT_SLURM_LABELS,
        result_labels=RESULT_LABELS,
    )
