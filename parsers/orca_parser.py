"""ORCA input and output parser."""

import os
import re

from .common import DEFAULT_SLURM_LABELS, format_standard_body, float_fmt, yes_no

TAG_FIELDS = ['job_type', 'functional', 'basis']
FILE_FIELDS = ['geometry_file', 'neb_product']

JOB_LABELS = [
    ('Project', 'project_label'),
    ('Code', 'code'),
    ('Input File', 'input_file'),
    ('Job Type', 'job_type'),
    ('Functional', 'functional'),
    ('Model', 'model'),
    ('Dispersion', 'dispersion'),
    ('Basis', 'basis'),
    ('Auxiliary Basis', 'aux_basis'),
    ('RI / Approximation', 'ri_mode'),
    ('Grid', 'grid'),
    ('Charge', 'charge'),
    ('Multiplicity', 'multiplicity'),
    ('Geometry File', 'geometry_file'),
    ('Parallel Procs', 'parallel_procs'),
    ('QM/MM', 'qmmm', yes_no),
    ('QM Atoms', 'qm_atoms'),
    ('NEB Product', 'neb_product'),
]

RESULT_LABELS = [
    ('Terminated Normally', 'terminated_normally', yes_no),
    ('ORCA Version', 'orca_version'),
    ('Final Energy (a.u.)', 'final_energy_au', float_fmt(10)),
    ('Final QM/QM2 Energy (a.u.)', 'final_qmmm_energy_au', float_fmt(10)),
    ('SCF Converged', 'scf_converged', yes_no),
    ('SCF Cycles', 'scf_cycles'),
    ('HOMO (eV)', 'homo_ev', float_fmt(4)),
    ('LUMO (eV)', 'lumo_ev', float_fmt(4)),
    ('Gap (eV)', 'gap_ev', float_fmt(4)),
    ('Total Run Time', 'total_run_time'),
]

KEYWORD_IGNORE = {
    'opt', 'verytightscf', 'verytightopt', 'anfreq', 'numfreq', 'fast-neb-ts',
    'pal8', 'pal4', 'pal2', 'pal16', 'pal32', 'rijcosx', 'defgrid3', 'defgrid2',
}


def parse_orca_input(inp_path: str) -> dict:
    metadata = {
        'code': 'ORCA',
        'input_file': os.path.basename(inp_path),
        'job_type': None,
        'functional': None,
        'model': None,
        'dispersion': None,
        'basis': None,
        'aux_basis': None,
        'ri_mode': None,
        'grid': None,
        'charge': None,
        'multiplicity': None,
        'geometry_file': None,
        'parallel_procs': None,
        'qmmm': False,
        'qm_atoms': None,
        'neb_product': None,
        'parse_errors': [],
    }
    try:
        with open(inp_path, 'r', errors='replace') as handle:
            raw = handle.read()
    except Exception as exc:
        metadata['parse_errors'].append(f'Could not read input file: {exc}')
        return metadata

    bang_tokens = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith('!'):
            bang_tokens.extend(stripped[1:].split())
    lower_tokens = [t.lower() for t in bang_tokens]

    for token in bang_tokens:
        lowered = token.lower()
        if '/' in token and metadata['model'] is None:
            metadata['model'] = token
        if lowered.startswith('def2-') and metadata['basis'] is None:
            metadata['basis'] = token
        elif lowered.startswith('def2/') and metadata['aux_basis'] is None:
            metadata['aux_basis'] = token
        elif lowered in ('rijcosx', 'cosx', 'ri', 'ri-j', 'rijk'):
            metadata['ri_mode'] = token
        elif lowered.startswith('defgrid') and metadata['grid'] is None:
            metadata['grid'] = token
        elif lowered in ('d3', 'd3bj', 'd4'):
            metadata['dispersion'] = token
    for token in bang_tokens:
        lowered = token.lower()
        if lowered in KEYWORD_IGNORE or lowered.startswith('pal'):
            continue
        if token.upper().startswith('QM/'):
            continue
        if metadata['functional'] is None and re.match(r'^[A-Za-z][A-Za-z0-9-]*$', token):
            metadata['functional'] = token
            break

    job_types = []
    if 'fast-neb-ts' in lower_tokens:
        job_types.append('NEB_TS')
    if 'opt' in lower_tokens:
        job_types.append('OPT')
    if 'anfreq' in lower_tokens or 'numfreq' in lower_tokens:
        job_types.append('FREQ')
    if not job_types:
        job_types.append('SINGLE_POINT')
    metadata['job_type'] = '+'.join(job_types)

    pal = re.search(r'!\s*.*?\bpal(\d+)\b', raw, re.IGNORECASE)
    if pal:
        metadata['parallel_procs'] = pal.group(1)
    xyzfile = re.search(r'\*\s*xyzfile\s+([+-]?\d+)\s+(\d+)\s+(\S+)', raw, re.IGNORECASE)
    if xyzfile:
        metadata['charge'] = xyzfile.group(1)
        metadata['multiplicity'] = xyzfile.group(2)
        metadata['geometry_file'] = xyzfile.group(3)
    else:
        xyz = re.search(r'\*\s*xyz\s+([+-]?\d+)\s+(\d+)', raw, re.IGNORECASE)
        if xyz:
            metadata['charge'] = xyz.group(1)
            metadata['multiplicity'] = xyz.group(2)
            metadata['geometry_file'] = 'inline geometry'
    qmmm = re.search(r'%qmmm(.*?)end', raw, re.IGNORECASE | re.DOTALL)
    if qmmm:
        metadata['qmmm'] = True
        atoms = re.search(r'QMAtoms\s*\{([^}]*)\}', qmmm.group(1), re.IGNORECASE)
        if atoms:
            metadata['qm_atoms'] = atoms.group(1).strip()
    neb = re.search(r'%neb(.*?)end', raw, re.IGNORECASE | re.DOTALL)
    if neb:
        product = re.search(r'product\s+"([^"]+)"', neb.group(1), re.IGNORECASE)
        if product:
            metadata['neb_product'] = product.group(1)
    return metadata


def _parse_last_orbital_table(content: str):
    blocks = list(re.finditer(r'ORBITAL ENERGIES\n-+\n\n\s*NO\s+OCC.*?(?=\n\n|\n\*Only)', content, re.DOTALL))
    if not blocks:
        return None, None, None
    table = blocks[-1].group(0)
    rows = re.findall(r'^\s*\d+\s+([\d.]+)\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)\s*$', table, re.MULTILINE)
    homo = lumo = None
    for occ, _eh, ev in rows:
        occ_val = float(occ)
        ev_val = float(ev)
        if occ_val > 0:
            homo = ev_val
        elif lumo is None:
            lumo = ev_val
    gap = (lumo - homo) if homo is not None and lumo is not None else None
    return homo, lumo, gap


def parse_orca_output(out_path: str) -> dict:
    results = {
        'terminated_normally': False,
        'orca_version': None,
        'final_energy_au': None,
        'final_qmmm_energy_au': None,
        'scf_converged': None,
        'scf_cycles': None,
        'homo_ev': None,
        'lumo_ev': None,
        'gap_ev': None,
        'total_run_time': None,
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

    version = re.search(r'Program Version\s+([\d.]+)', content)
    if version:
        results['orca_version'] = version.group(1)
    energies = re.findall(r'FINAL SINGLE POINT ENERGY\s+([+-]?\d+\.\d+)', content)
    qmmm_energies = re.findall(r'FINAL SINGLE POINT ENERGY \(QM/QM2\)\s+([+-]?\d+\.\d+)', content)
    if energies:
        results['final_energy_au'] = float(energies[-1])
    if qmmm_energies:
        results['final_qmmm_energy_au'] = float(qmmm_energies[-1])
    scf = re.findall(r'SCF CONVERGED AFTER\s+(\d+)\s+CYCLES', content)
    if scf:
        results['scf_converged'] = True
        results['scf_cycles'] = int(scf[-1])
    elif 'SCF NOT CONVERGED' in content.upper():
        results['scf_converged'] = False
    homo, lumo, gap = _parse_last_orbital_table(content)
    results['homo_ev'], results['lumo_ev'], results['gap_ev'] = homo, lumo, gap
    runtime = re.search(r'TOTAL RUN TIME:\s*(.+)', content)
    if runtime:
        results['total_run_time'] = runtime.group(1).strip()
    error_markers = ['ORCA finished by error termination', 'ERROR !!!', 'ABORTING THE RUN']
    results['terminated_normally'] = ('ORCA TERMINATED NORMALLY' in content) or (
        results['final_energy_au'] is not None and not any(marker in content for marker in error_markers)
    )
    return results


def format_elabftw_body_orca(input_meta: dict, slurm_meta: dict, output_results=None) -> str:
    return format_standard_body(
        input_meta,
        slurm_meta,
        output_results,
        job_labels=JOB_LABELS,
        slurm_labels=DEFAULT_SLURM_LABELS,
        result_labels=RESULT_LABELS,
    )
