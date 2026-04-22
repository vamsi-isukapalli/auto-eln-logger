#!/usr/bin/env python3
"""Job-end epilog used by bmdsubmit."""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from api.elabftw_client import ElabFTWClient, STATUS_FAIL, STATUS_REDO, STATUS_SUCCESS
from parsers import collect_referenced_files, format_body_for_code, parse_input_for_code, parse_output_for_code
from parsers.common import HARDWARE_LABELS, append_marked_section, resolve_relative_file, section_table_if_any

LOG_FILE = Path.home() / '.bmdeln' / 'epilog.log'
STATE_DIR = Path.home() / '.bmdeln' / 'jobs'
MAX_UPLOAD_MB = float(os.environ.get('BMDELN_MAX_UPLOAD_MB', '10'))


def log(message: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] EPILOG | {message}'
    print(line, file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as handle:
        handle.write(line + '\n')


def file_size_mb(path: str) -> float:
    try:
        return Path(path).stat().st_size / (1024 * 1024)
    except Exception:
        return 0.0


def query_sacct(job_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        result = subprocess.run(
            ['sacct', '-j', job_id, '--format=JobIDRaw,State,Elapsed,ExitCode', '--parsable2', '--noheader'],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None, None, None
    if result.returncode != 0:
        return None, None, None

    records = []
    for line in result.stdout.splitlines():
        parts = line.strip().split('|')
        if len(parts) < 4:
            continue
        jid, state, elapsed, exit_code = parts[:4]
        if jid == job_id or jid.startswith(f'{job_id}.'):
            records.append((jid, state or None, elapsed or None, exit_code or None))
    if not records:
        return None, None, None

    terminal_order = ['CANCELLED', 'TIMEOUT', 'FAILED', 'NODE_FAIL', 'OUT_OF_MEMORY', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'COMPLETED']

    def rank(state: Optional[str]) -> int:
        text = (state or '').upper()
        for idx, prefix in enumerate(terminal_order):
            if text.startswith(prefix):
                return idx
        return len(terminal_order)

    best = sorted(records, key=lambda row: (rank(row[1]), 0 if row[0] == job_id else 1))[0]
    return best[1], best[2], best[3]


def determine_success(output_results, slurm_state: Optional[str], script_exit_code: Optional[str], cancelled: bool, workflow_type: str = 'single') -> Tuple[bool, bool]:
    parser_success = None if output_results is None else output_results.get('terminated_normally')
    script_success = None if script_exit_code in (None, '') else str(script_exit_code) == '0'
    slurm_success = None if not slurm_state else slurm_state.upper().startswith('COMPLETED')
    manual_review = False

    if cancelled:
        return False, manual_review
    if parser_success is False:
        return False, manual_review
    if slurm_state and any(slurm_state.upper().startswith(prefix) for prefix in ('CANCELLED', 'FAILED', 'TIMEOUT', 'OUT_OF_MEMORY', 'PREEMPTED', 'NODE_FAIL', 'BOOT_FAIL', 'DEADLINE')):
        return False, manual_review
    if script_success is False:
        return False, manual_review
    if parser_success is True:
        return True, manual_review
    if slurm_success is True and script_success in (True, None):
        manual_review = True
        return True, manual_review
    return False, manual_review


def read_lscpu() -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    try:
        result = subprocess.run(['lscpu'], capture_output=True, text=True, timeout=5)
    except Exception:
        return metadata
    if result.returncode != 0:
        return metadata
    mapping = {
        'Model name:': 'cpu_model',
        'Architecture:': 'architecture',
        'Socket(s):': 'sockets',
        'Core(s) per socket:': 'cores_per_socket',
        'Thread(s) per core:': 'threads_per_core',
        'CPU(s):': 'total_threads',
    }
    for line in result.stdout.splitlines():
        for prefix, key in mapping.items():
            if line.startswith(prefix):
                metadata[key] = line.split(':', 1)[1].strip()
    return metadata


def read_mem_total_mb() -> Optional[str]:
    try:
        with open('/proc/meminfo') as handle:
            for line in handle:
                if line.startswith('MemTotal:'):
                    kb = int(line.split()[1])
                    return str(kb // 1024)
    except Exception:
        return None
    return None


def gather_hardware_metadata() -> Dict[str, str]:
    metadata = read_lscpu()
    metadata['hostname'] = os.environ.get('SLURMD_NODENAME') or os.environ.get('HOSTNAME') or ''
    metadata['mem_total_mb'] = read_mem_total_mb() or ''
    try:
        if metadata.get('sockets') and metadata.get('cores_per_socket'):
            metadata['total_cores'] = str(int(metadata['sockets']) * int(metadata['cores_per_socket']))
    except Exception:
        pass
    return {k: v for k, v in metadata.items() if v not in (None, '', [])}


def expand_slurm_pattern(pattern: str, slurm_meta: dict, job_id: str, submit_dir: str) -> str:
    values = {
        '%j': job_id,
        '%A': job_id,
        '%a': os.environ.get('SLURM_ARRAY_TASK_ID', '0'),
        '%u': slurm_meta.get('user', os.environ.get('USER', 'unknown')),
        '%x': slurm_meta.get('job_name', 'job'),
        '%N': os.environ.get('SLURMD_NODENAME', 'node'),
    }
    expanded = pattern
    for key, value in values.items():
        expanded = expanded.replace(key, str(value))
    path = Path(expanded)
    if not path.is_absolute():
        path = Path(submit_dir) / path
    return str(path)


def expected_slurm_files(slurm_meta: dict, job_id: str, submit_dir: str) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    output_directive = (slurm_meta.get('output_directive') or '').strip()
    error_directive = (slurm_meta.get('error_directive') or '').strip()
    if output_directive:
        candidates.append((expand_slurm_pattern(output_directive, slurm_meta, job_id, submit_dir), 'Slurm stdout'))
    else:
        candidates.append((str(Path(submit_dir) / f'slurm-{job_id}.out'), 'Slurm stdout'))
    if error_directive:
        candidates.append((expand_slurm_pattern(error_directive, slurm_meta, job_id, submit_dir), 'Slurm stderr'))
    return candidates


def normalize_amber_steps_for_final_state(input_meta: dict, overall_success: bool, cancelled: bool):
    steps = input_meta.get('amber_steps') or []
    if not steps:
        return
    for step in steps:
        status = step.get('status')
        if status == 'RUNNING':
            if cancelled:
                step['status'] = 'INTERRUPTED'
            else:
                step['status'] = 'COMPLETED' if overall_success else 'FAILED'
            step['finished_at'] = step.get('finished_at') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        elif not status:
            step['status'] = 'PENDING'


def parse_elapsed_to_seconds(elapsed: Optional[str]) -> Optional[float]:
    if not elapsed:
        return None
    text = elapsed.strip()
    try:
        if '-' in text:
            days, hms = text.split('-', 1)
            hours, minutes, seconds = [int(x) for x in hms.split(':')]
            return float(int(days) * 86400 + hours * 3600 + minutes * 60 + seconds)
        parts = [int(x) for x in text.split(':')]
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return float(hours * 3600 + minutes * 60 + seconds)
        if len(parts) == 2:
            minutes, seconds = parts
            return float(minutes * 60 + seconds)
    except Exception:
        return None
    return None


def enrich_cp2k_results(input_meta: dict, output_results: Optional[dict], elapsed: Optional[str]):
    if not output_results:
        return
    if output_results.get('md_steps_completed') in (None, ''):
        try:
            if str(input_meta.get('run_type', '')).upper() == 'MD' and output_results.get('terminated_normally'):
                output_results['md_steps_completed'] = int(input_meta.get('md_steps'))
        except Exception:
            pass
    if output_results.get('scf_converged') is None and output_results.get('terminated_normally') and output_results.get('final_energy_au') is not None:
        output_results['scf_converged'] = True
    if output_results.get('total_time_s') is None:
        total_seconds = parse_elapsed_to_seconds(elapsed)
        if total_seconds is not None:
            output_results['total_time_s'] = total_seconds


def candidate_stems(code: str, input_meta: dict, input_file: str, output_file: str) -> List[str]:
    stems: List[str] = []

    def add(value: Optional[str]):
        if not value:
            return
        text = str(value).strip()
        if not text:
            return
        stem = Path(text).stem if any(sep in text for sep in ('/', '\\')) or '.' in text else text
        if stem and stem not in stems:
            stems.append(stem)

    add(Path(input_file).stem)
    add(Path(output_file).stem if output_file else None)
    add(input_meta.get('project_name'))
    add(input_meta.get('project_label'))
    if code == 'Amber':
        for step in input_meta.get('amber_steps', []):
            add(step.get('display_name'))
            add(step.get('output_file'))
            add(step.get('input_file'))
    return stems


def iter_job_specific_matches(code: str, submit_dir: str, input_meta: dict, input_file: str, output_file: str):
    base = Path(submit_dir)
    stems = candidate_stems(code, input_meta, input_file, output_file)
    seen = set()

    def emit(path_obj: Path, label: str, always_reference: bool):
        resolved = str(path_obj)
        if resolved in seen or not path_obj.exists():
            return None
        seen.add(resolved)
        return resolved, label, always_reference

    if code == 'CP2K':
        for stem in stems:
            for pattern in [
                f'{stem}-pos-*.xyz',
                f'{stem}-vel-*.xyz',
                f'{stem}-frc-*.xyz',
                f'{stem}*TRAJ*',
                f'{stem}*.dcd',
                f'{stem}*.xtc',
            ]:
                for match in base.glob(pattern):
                    item = emit(match, f'Additional file: {match.name}', True)
                    if item:
                        yield item
    elif code == 'ORCA':
        for stem in stems:
            for pattern in [f'{stem}*.gbw', f'{stem}*.engrad', f'{stem}*.hess', f'{stem}*.trj']:
                for match in base.glob(pattern):
                    item = emit(match, f'Additional file: {match.name}', True)
                    if item:
                        yield item
    elif code == 'Amber':
        for stem in stems:
            for pattern in [f'{stem}*.mdinfo']:
                for match in base.glob(pattern):
                    item = emit(match, f'Additional file: {match.name}', False)
                    if item:
                        yield item
    elif code == 'OpenMolcas':
        for stem in stems:
            for pattern in [f'{stem}*.RasOrb', f'{stem}*.molden', f'{stem}*.h5', f'{stem}*.RunFile']:
                for match in base.glob(pattern):
                    item = emit(match, f'Additional file: {match.name}', True)
                    if item:
                        yield item
    elif code == 'Molpro':
        for stem in stems:
            for pattern in [f'{stem}*.wfu', f'{stem}*.xml']:
                for match in base.glob(pattern):
                    item = emit(match, f'Additional file: {match.name}', True)
                    if item:
                        yield item




def infer_workflow_progress(input_meta: dict, slurm_log_path: str) -> dict:
    workflow_type = input_meta.get('workflow_type', 'single')
    steps = input_meta.get('workflow_steps') or []
    results = {
        'terminated_normally': None,
        'warnings': [],
    }
    if workflow_type == 'single' or not slurm_log_path or not Path(slurm_log_path).exists():
        return results
    try:
        content = Path(slurm_log_path).read_text(errors='replace')
    except Exception:
        return results
    if workflow_type == 'loop':
        started = re.findall(r'^---\s+Starting:\s+(.+?)\s+at', content, re.MULTILINE)
        finished = re.findall(r'^---\s+Finished:\s+(.+?)\s+---', content, re.MULTILINE)
        completed_set = set(finished)
        for step in steps:
            name = str(step.get('display_name') or '').strip()
            if name in completed_set:
                step['status'] = 'COMPLETED'
            elif name in started:
                step['status'] = 'RUNNING'
        input_meta['workflow_steps'] = steps
        input_meta['workflow_completed_steps'] = len(completed_set)
        input_meta['workflow_started_steps'] = len(set(started))
        results['terminated_normally'] = 'All ' in content and 'completed' in content.lower()
        if 'failed' in content.lower() or 'stopping loop' in content.lower():
            results['warnings'].append('Loop workflow reported a failure in the Slurm log.')
    elif workflow_type == 'array':
        finished = re.findall(r'Task\s+(\d+)\s+\((.+?)\):\s+finished!', content)
        completed = {(tid, name) for tid, name in finished}
        for step in steps:
            name = str(step.get('display_name') or '')
            idx = str(step.get('step_index', 0) + 1)
            if (idx, name) in completed:
                step['status'] = 'COMPLETED'
        input_meta['workflow_steps'] = steps
        input_meta['workflow_completed_steps'] = len(completed)
        results['terminated_normally'] = len(completed) > 0
        if 'ERROR: Task' in content:
            results['warnings'].append('At least one array task reported an error in the Slurm log.')
    return results

def gather_files(code: str, input_meta: dict, submit_dir: str, input_file: str, output_file: str,
                 slurm_meta: dict, slurm_job_id: str, submit_script: str = '') -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    files_to_upload: List[Tuple[str, str]] = []
    large_file_paths: List[Tuple[str, str]] = []
    seen: set[str] = set()

    def add_file(path: str, label: str, always_reference: bool = False):
        if not path:
            return
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = Path(submit_dir) / resolved
        resolved_str = str(resolved)
        if resolved_str in seen:
            return
        seen.add(resolved_str)
        if not resolved.exists():
            large_file_paths.append((resolved_str, f'{label} (not found)'))
            return
        size = file_size_mb(resolved_str)
        if always_reference or size > MAX_UPLOAD_MB:
            suffix = 'access on cluster' if resolved.exists() else 'not found'
            large_file_paths.append((resolved_str, f'{label} ({size:.1f} MB — {suffix})'))
        else:
            files_to_upload.append((resolved_str, label if size == 0 else f'{label} ({size:.1f} MB)'))

    add_file(input_file, f'{code} input file')
    add_file(submit_script, 'Slurm submit script')
    add_file(output_file, f'{code} output log')

    for path, label in expected_slurm_files(slurm_meta, slurm_job_id, submit_dir):
        add_file(path, label)

    for relpath in collect_referenced_files(code, input_meta):
        add_file(resolve_relative_file(submit_dir, relpath), f'Referenced file: {Path(relpath).name}')

    if code == 'Amber':
        for step in input_meta.get('amber_steps', []):
            add_file(step.get('input_file') or '', f"Amber step input: {step.get('step_id', '')}")
            add_file(step.get('output_file') or '', f"Amber step output: {step.get('step_id', '')}")
            add_file(step.get('restart_out') or '', f"Amber restart: {step.get('step_id', '')}", always_reference=True)
            add_file(step.get('trajectory_file') or '', f"Amber trajectory: {step.get('step_id', '')}", always_reference=True)

    for path, label, always_reference in iter_job_specific_matches(code, submit_dir, input_meta, input_file, output_file):
        add_file(path, label, always_reference=always_reference)
    return files_to_upload, large_file_paths


def cluster_paths_html(large_file_paths: List[Tuple[str, str]]) -> str:
    if not large_file_paths:
        return ''
    rows = ''.join(
        f"<tr><td>{html.escape(label)}</td><td><code>{html.escape(path)}</code></td></tr>"
        for path, label in large_file_paths
    )
    return (
        '<h2>Data Files on Cluster</h2>'
        '<p><i>Files too large to upload or intentionally kept on the cluster.</i></p>'
        "<table border='1' cellpadding='5' cellspacing='0'>"
        '<tr><th>File</th><th>Path on Cluster</th></tr>'
        f'{rows}</table>'
    )


def safe_update_experiment(client: ElabFTWClient, exp_id: int, body: str, status_id: Optional[int]) -> bool:
    client.update_experiment(exp_id, body=body, status_id=status_id)
    return True


def main():
    exp_id_str = os.environ.get('BMDELN_EXP_ID', '').strip()
    code = os.environ.get('BMDELN_CODE', 'Unknown').strip()
    input_file = os.environ.get('BMDELN_INPUT_FILE', '').strip()
    output_file = os.environ.get('BMDELN_OUTPUT_FILE', '').strip()
    submit_dir = os.environ.get('BMDELN_SUBMIT_DIR', '').strip() or str(Path.cwd())
    slurm_job_id = os.environ.get('SLURM_JOB_ID', '').strip() or 'unknown'
    script_exit_code = os.environ.get('BMDELN_SCRIPT_EXIT_CODE', '').strip()
    cancelled = os.environ.get('BMDELN_CANCELLED', '').strip() in ('1', 'true', 'TRUE', 'yes', 'YES')
    log(f'Starting epilog for job {slurm_job_id}, eLabFTW exp={exp_id_str}, code={code}')
    if not exp_id_str:
        log('No BMDELN_EXP_ID set — skipping eLabFTW update.')
        return

    exp_id = int(exp_id_str)
    client = ElabFTWClient()
    state_file = STATE_DIR / f'{slurm_job_id}.json'
    input_meta = {}
    slurm_meta = {}
    if state_file.exists():
        with open(state_file) as handle:
            state = json.load(handle)
        input_meta = state.get('input_meta', {})
        slurm_meta = state.get('slurm_meta', {})
    else:
        log(f'State file not found: {state_file}. Re-parsing input.')
        input_meta = parse_input_for_code(code, input_file)
        slurm_meta = {'job_id': slurm_job_id, 'submit_dir': submit_dir}

    workflow_type = input_meta.get('workflow_type', 'single')
    output_results = None if workflow_type in ('loop', 'array') else parse_output_for_code(code, output_file)
    for path, label in expected_slurm_files(slurm_meta, slurm_job_id, submit_dir):
        if 'stdout' in label.lower():
            wf_results = infer_workflow_progress(input_meta, path)
            if workflow_type in ('loop', 'array'):
                output_results = wf_results
            break
    slurm_state, elapsed, sacct_exit = query_sacct(slurm_job_id) if slurm_job_id and slurm_job_id != 'unknown' else (None, None, None)
    job_success, manual_review = determine_success(output_results, slurm_state, script_exit_code or sacct_exit, cancelled, workflow_type=workflow_type)

    effective_state = slurm_state or ('COMPLETED' if job_success else 'FAILED')
    if cancelled and not (slurm_state or '').upper().startswith('CANCELLED'):
        effective_state = 'CANCELLED'
    slurm_meta['status'] = effective_state
    slurm_meta['actual_walltime'] = elapsed or os.environ.get('SLURM_JOB_ELAPSED', '—')
    slurm_meta['job_id'] = slurm_job_id

    if code == 'Amber':
        normalize_amber_steps_for_final_state(input_meta, job_success, cancelled)
    elif code == 'CP2K':
        enrich_cp2k_results(input_meta, output_results, slurm_meta.get('actual_walltime'))

    updated_body = format_body_for_code(code, input_meta, slurm_meta, output_results)

    hardware_meta = gather_hardware_metadata()
    if hardware_meta:
        updated_body = append_marked_section(updated_body, 'BMDELN_HARDWARE', section_table_if_any('Hardware', hardware_meta, HARDWARE_LABELS))

    submit_script = state.get('submit_script', '') if 'state' in locals() else os.environ.get('BMDELN_SUBMIT_SCRIPT', '')
    files_to_upload, large_file_paths = gather_files(code, input_meta, submit_dir, input_file, output_file, slurm_meta, slurm_job_id, submit_script=submit_script)
    if large_file_paths:
        updated_body = append_marked_section(updated_body, 'BMDELN_DATA_FILES', cluster_paths_html(large_file_paths))

    update_ok = False
    status_id = STATUS_REDO if manual_review else (STATUS_SUCCESS if job_success else STATUS_FAIL)
    try:
        safe_update_experiment(client, exp_id, updated_body, status_id)
        update_ok = True
        log(f'Updated eLabFTW entry {exp_id} — status: {slurm_meta["status"]}')
    except Exception as exc:
        log(f'ERROR updating eLabFTW entry: {exc}')

    for path, comment in files_to_upload:
        try:
            client.upload_file(exp_id, path, comment=comment)
            log(f'Uploaded: {path}')
        except Exception as exc:
            log(f'WARNING: Could not upload {path}: {exc}')
            update_ok = False

    try:
        result_summary = ''
        if output_results and output_results.get('final_energy_au') is not None:
            result_summary = f" | Final energy: {output_results['final_energy_au']:.6f} a.u."
        elif output_results and output_results.get('final_etot') is not None:
            result_summary = f" | Final Etot: {output_results['final_etot']:.4f}"
        client.add_comment(
            exp_id,
            f"[BMDELN AUTO] Job {slurm_job_id} completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — Status: {slurm_meta['status']}{result_summary}",
        )
    except Exception as exc:
        log(f'WARNING: Could not add comment: {exc}')
        update_ok = False

    if state_file.exists():
        try:
            with open(state_file, 'w') as handle:
                json.dump({
                    'exp_id': exp_id,
                    'slurm_job_id': slurm_job_id,
                    'code': code,
                    'input_file': input_file,
                    'output_file': output_file,
                    'submit_dir': submit_dir,
                    'input_meta': input_meta,
                    'slurm_meta': slurm_meta,
                    'workflow': state.get('workflow', {}),
                }, handle, indent=2)
        except Exception:
            pass

    if update_ok and state_file.exists():
        try:
            state_file.unlink()
        except Exception:
            pass
    log('Epilog complete.')


if __name__ == '__main__':
    main()
