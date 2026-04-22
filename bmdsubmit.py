#!/usr/bin/env python3
"""bmdsubmit — ELN-aware Slurm submit wrapper (Version 3).

Supports:
- single-run jobs
- script-only submission when the real inputs are inferred from the submitter
- loop workflows
- array workflows
- Amber multi-step jobs
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from api.elabftw_client import ElabFTWClient, STATUS_FAIL, STATUS_QUEUED
from parsers import (
    build_tags_for_code,
    format_body_for_code,
    guess_output_file,
    parse_input_for_code,
    parse_script_steps_for_code,
)
from parsers.code_detector import detect_code
from parsers.workflow_detector import detect_workflow

REAL_SBATCH = os.environ.get('REAL_SBATCH') or shutil.which('sbatch') or '/usr/bin/sbatch'
EPILOG_SCRIPT = str(SCRIPT_DIR / 'scripts' / 'bmdeln_epilog.py')
STAGE_UPDATE_SCRIPT = str(SCRIPT_DIR / 'scripts' / 'bmdeln_stage_update.py')
JOB_EVENT_SCRIPT = str(SCRIPT_DIR / 'scripts' / 'bmdeln_job_event.py')
STATE_DIR = Path.home() / '.bmdeln' / 'jobs'
LOG_FILE = Path.home() / '.bmdeln' / 'bmdsubmit.log'
DEFAULT_PROJECT = os.environ.get('BMDELN_DEFAULT_PROJECT', '').strip()

EXAMPLES = """
Examples:
  bmdsubmit input.inp submit.sh
  bmdsubmit input.inp submit.slurm
  bmdsubmit submit.sh                    # infer real inputs from the submitter
  bmdsubmit --project water input.inp submit.sh
  bmdsubmit --project scan submit.sh -- --qos=debug

Recommended usage:
  - Standard single job: pass both input file and submit script.
  - Loop / array / workflow jobs: passing only the submit script is supported.
  - Extra sbatch arguments can be added after --.
"""


def log(message: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {message}'
    print(line, file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as handle:
        handle.write(line + '\n')


def parse_sbatch_directives(script_content: str) -> dict:
    directives = {}
    short_map = {
        '-J': 'job_name', '-p': 'partition', '-N': 'nodes', '-n': 'ntasks', '-t': 'time',
        '-o': 'output', '-e': 'error', '--time': 'time', '--mem': 'mem',
    }
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
        key = short_map.get(flag)
        if key:
            directives[key] = value
    return directives


def find_submit_script(submit_dir: Path, explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit)
    candidates = sorted({*submit_dir.glob('*.sh'), *submit_dir.glob('*.slurm')})
    if len(candidates) == 1:
        log(f'Auto-detected submit script: {candidates[0].name}')
        return candidates[0]
    raise RuntimeError('Could not auto-detect submit script. Pass it explicitly or use bmdsubmit --help.')


def resolve_cli_files(submit_dir: Path, arg1: Optional[str], arg2: Optional[str]) -> Tuple[Optional[Path], Path]:
    if arg1 is None and arg2 is None:
        raise RuntimeError('No arguments provided. Run bmdsubmit --help for usage examples.')
    input_path: Optional[Path] = None
    submit_script: Optional[Path] = None

    if arg2 is not None:
        input_path = submit_dir / arg1 if arg1 else None
        submit_script = submit_dir / arg2
        return input_path, submit_script

    candidate = submit_dir / str(arg1)
    if candidate.suffix in ('.sh', '.slurm') and candidate.exists():
        submit_script = candidate
        return None, submit_script

    input_path = candidate
    submit_script = find_submit_script(submit_dir, None)
    return input_path, submit_script


def parse_launch_details(script_content: str) -> Tuple[Optional[str], Optional[str]]:
    launch_command = None
    launcher_tasks = None
    for line in script_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if re.search(r'\b(cp2k|orca|pmemd|sander|molpro|pymolcas)\b', stripped, re.IGNORECASE):
            launch_command = stripped
            break
    mpi = re.search(r'\bmpiexec\b.*?\s-np\s+(\d+)', script_content)
    if mpi:
        launcher_tasks = mpi.group(1)
    else:
        srun = re.search(r'\bsrun\b.*?\s-n\s+(\d+)', script_content)
        if srun:
            launcher_tasks = srun.group(1)
    return launch_command, launcher_tasks


def representative_name(input_meta: dict, input_file: str, project_label: str) -> str:
    if project_label:
        return project_label
    for key in ('project_name', 'title', 'job_type', 'run_type', 'workflow_description'):
        value = input_meta.get(key)
        if value:
            return str(value)
    return input_file or 'workflow'


def choose_project_label(input_meta: dict, submit_dir: Path, explicit_project: Optional[str]) -> str:
    if explicit_project:
        return explicit_project.strip()
    if input_meta.get('project_name'):
        return str(input_meta['project_name']).strip()
    if DEFAULT_PROJECT:
        return DEFAULT_PROJECT
    return submit_dir.name


def inject_epilog(script_content: str, env_map: Dict[str, str]) -> str:
    lines = script_content.splitlines()
    insert_at = 0
    if lines and lines[0].startswith('#!'):
        insert_at = 1
    while insert_at < len(lines):
        stripped = lines[insert_at].strip()
        if not stripped or stripped.startswith('#SBATCH') or stripped.startswith('#'):
            insert_at += 1
            continue
        break
    exports = [f"export {key}={shlex.quote(value)}" for key, value in env_map.items()]
    block = [
        '',
        '# --- BMDELN AUTO-INJECTED START ---',
        *exports,
        'bmdeln_cancel_trap() {',
        '    export BMDELN_CANCELLED=1',
        '    exit 143',
        '}',
        'trap bmdeln_cancel_trap TERM INT',
        'bmdeln_epilog_trap() {',
        '    local bmdeln_status=$?',
        '    export BMDELN_SCRIPT_EXIT_CODE="$bmdeln_status"',
        f'    python3 {shlex.quote(EPILOG_SCRIPT)} || true',
        '}',
        'trap bmdeln_epilog_trap EXIT',
        f'python3 {shlex.quote(JOB_EVENT_SCRIPT)} --job-id "${{SLURM_JOB_ID:-}}" --event start || true',
        '# --- BMDELN AUTO-INJECTED END ---',
        '',
    ]
    lines[insert_at:insert_at] = block
    return '\n'.join(lines) + '\n'


def inject_amber_stage_updates(script_content: str, stages: List[dict]) -> str:
    if not stages:
        return script_content
    lines = script_content.splitlines()
    updated: List[str] = []
    stage_idx = 0
    amber_cmd_re = re.compile(r'\b(?:pmemd(?:\.MPI)?|sander(?:\.MPI)?)\b', re.IGNORECASE)
    for line in lines:
        stripped = line.strip()
        if stage_idx < len(stages) and stripped and not stripped.startswith('#') and amber_cmd_re.search(stripped):
            indent = re.match(r'^(\s*)', line).group(1)
            updated.append(f"{indent}# --- BMDELN AMBER STAGE START ---")
            updated.append(f"{indent}python3 {shlex.quote(STAGE_UPDATE_SCRIPT)} --job-id \"${{SLURM_JOB_ID:-}}\" --stage-index {stage_idx} --event start || true")
            updated.append(line)
            updated.append(f'{indent}bmdeln_stage_rc=$?')
            updated.append(f"{indent}python3 {shlex.quote(STAGE_UPDATE_SCRIPT)} --job-id \"${{SLURM_JOB_ID:-}}\" --stage-index {stage_idx} --event finish --exit-code \"$bmdeln_stage_rc\" || true")
            updated.append(f'{indent}if [ "$bmdeln_stage_rc" -ne 0 ]; then')
            updated.append(f'{indent}    exit "$bmdeln_stage_rc"')
            updated.append(f'{indent}fi')
            updated.append(f"{indent}# --- BMDELN AMBER STAGE END ---")
            stage_idx += 1
        else:
            updated.append(line)
    return '\n'.join(updated) + ('\n' if script_content.endswith('\n') else '')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='BMD ELN-aware Slurm job submitter',
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--project', help='Project label/tag for this job', default=None)
    parser.add_argument('arg1', nargs='?', help='Input file or submit script')
    parser.add_argument('arg2', nargs='?', help='Submit script if arg1 is the input file')
    parser.add_argument('extra_args', nargs=argparse.REMAINDER, help='Extra args passed to sbatch after --')
    args = parser.parse_args()

    if args.arg1 is None and args.arg2 is None:
        parser.print_help()
        return 0

    submit_dir = Path.cwd()
    try:
        input_path, submit_script = resolve_cli_files(submit_dir, args.arg1, args.arg2)
    except Exception as exc:
        log(f'ERROR: {exc}')
        return 1
    if not submit_script.is_file():
        log(f'ERROR: Submit script not found: {submit_script}')
        return 1
    if input_path is not None and not input_path.is_file():
        log(f'ERROR: Input file not found: {input_path}')
        return 1
    if not Path(REAL_SBATCH).exists():
        log(f'ERROR: Could not find sbatch at {REAL_SBATCH}. Set REAL_SBATCH in ~/.bmdeln/config.env')
        return 1

    script_content = submit_script.read_text(errors='replace')
    workflow = detect_workflow(script_content, submit_dir)
    explicit_input_name = input_path.name if input_path else ''
    detected_code = workflow.get('code_hint') or detect_code(script_content, explicit_input_name)
    log(f'Detected QC code: {detected_code}')

    effective_input: Optional[Path] = input_path
    if effective_input is None:
        for candidate in workflow.get('primary_inputs', []):
            p = Path(candidate)
            if not p.is_absolute():
                p = submit_dir / p
            if p.exists():
                effective_input = p
                break

    if effective_input is not None:
        input_meta = parse_input_for_code(detected_code, str(effective_input))
    else:
        input_meta = {'code': detected_code, 'input_file': '', 'parse_errors': []}
    input_meta['project_label'] = choose_project_label(input_meta, submit_dir, args.project)
    input_meta['workflow_type'] = workflow.get('workflow_type', 'single')
    input_meta['workflow_description'] = workflow.get('description', 'Single-run job')
    input_meta['workflow_step_source'] = workflow.get('step_source_file')
    input_meta['workflow_step_count'] = workflow.get('step_count')
    input_meta['workflow_array_spec'] = workflow.get('array_spec')
    input_meta['workflow_primary_inputs'] = workflow.get('primary_inputs', [])
    input_meta['workflow_steps'] = workflow.get('steps', [])

    amber_steps = parse_script_steps_for_code(detected_code, script_content)
    if amber_steps:
        input_meta['amber_steps'] = amber_steps

    directives = parse_sbatch_directives(script_content)
    launch_command, launcher_tasks = parse_launch_details(script_content)
    slurm_meta = {
        'job_name': directives.get('job_name', submit_dir.name).split()[0],
        'partition': directives.get('partition', 'unknown').split()[0],
        'nodes': directives.get('nodes', '?').split()[0],
        'ntasks': directives.get('ntasks', '?').split()[0],
        'time_limit': directives.get('time', '?').split()[0],
        'mem': directives.get('mem', '?').split()[0],
        'user': os.environ.get('USER', 'unknown'),
        'submit_dir': str(submit_dir),
        'status': 'IN QUEUE' if STATUS_QUEUED != STATUS_FAIL else 'RUNNING',
        'job_id': None,
        'launch_command': launch_command,
        'launcher_tasks': launcher_tasks,
        'actual_walltime': None,
        'output_directive': directives.get('output'),
        'error_directive': directives.get('error'),
    }

    input_display = effective_input.name if effective_input else submit_script.name
    body = format_body_for_code(detected_code, input_meta, slurm_meta)
    title = representative_name(input_meta, input_display, input_meta.get('project_label', ''))
    tags = build_tags_for_code(detected_code, input_meta, slurm_meta)

    try:
        client = ElabFTWClient()
        exp_id = client.create_experiment(title=title, body=body, tags=tags)
        log(f'Created eLabFTW entry: ID={exp_id}')
    except Exception as exc:
        log(f'ERROR creating eLabFTW entry: {exc}')
        return 1

    output_file = guess_output_file(detected_code, str(submit_dir), str(effective_input.name if effective_input else submit_script.name), directives, script_content, workflow=workflow)

    env_map = {
        'BMDELN_EXP_ID': str(exp_id),
        'BMDELN_CODE': detected_code,
        'BMDELN_INPUT_FILE': str(effective_input if effective_input else ''),
        'BMDELN_OUTPUT_FILE': str(output_file),
        'BMDELN_SUBMIT_DIR': str(submit_dir),
        'BMDELN_SUBMIT_SCRIPT': str(submit_script),
        'BMDELN_WORKFLOW_TYPE': str(workflow.get('workflow_type', 'single')),
    }
    temp_script_content = inject_epilog(script_content, env_map)
    if detected_code == 'Amber' and amber_steps:
        temp_script_content = inject_amber_stage_updates(temp_script_content, amber_steps)

    with tempfile.NamedTemporaryFile('w', delete=False, prefix='.bmdeln_tmp_', suffix='.sh', dir=submit_dir) as handle:
        handle.write(temp_script_content)
        temp_script_path = Path(handle.name)
    temp_script_path.chmod(0o755)

    sbatch_cmd = [REAL_SBATCH, str(temp_script_path)]
    if args.extra_args and args.extra_args[0] == '--':
        sbatch_cmd.extend(args.extra_args[1:])
    elif args.extra_args:
        sbatch_cmd.extend(args.extra_args)

    log(f'Submitting: {shlex.join(sbatch_cmd)}')
    result = subprocess.run(sbatch_cmd, capture_output=True, text=True)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    if result.returncode != 0:
        try:
            client.update_experiment(exp_id, body=body + '<p style="color:red"><b>Submission to Slurm failed.</b></p>', status_id=STATUS_FAIL)
        except Exception:
            pass
        try:
            temp_script_path.unlink(missing_ok=True)
        except Exception:
            pass
        return result.returncode

    match = re.search(r'Submitted batch job\s+(\d+)', stdout)
    if not match:
        log('ERROR: Could not parse Slurm job ID from sbatch output')
        try:
            temp_script_path.unlink(missing_ok=True)
        except Exception:
            pass
        return 1

    slurm_job_id = match.group(1)
    slurm_meta['job_id'] = slurm_job_id
    body = format_body_for_code(detected_code, input_meta, slurm_meta)
    try:
        client.update_experiment(exp_id, body=body, status_id=STATUS_QUEUED)
        log(f'Updated eLabFTW entry {exp_id} with Slurm Job ID {slurm_job_id}')
    except Exception as exc:
        log(f'WARNING: Could not update eLabFTW entry with Slurm job metadata: {exc}')

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        'exp_id': exp_id,
        'slurm_job_id': slurm_job_id,
        'code': detected_code,
        'input_file': str(effective_input) if effective_input else '',
        'output_file': str(output_file),
        'submit_dir': str(submit_dir),
        'submit_script': submit_script.name,
        'input_meta': input_meta,
        'slurm_meta': slurm_meta,
        'sbatch_directives': directives,
        'workflow': workflow,
    }
    state_path = STATE_DIR / f'{slurm_job_id}.json'
    with open(state_path, 'w') as handle:
        json.dump(state, handle, indent=2)
    log(f'State saved: {state_path}')

    try:
        temp_script_path.unlink(missing_ok=True)
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
