#!/usr/bin/env python3
"""Job lifecycle updater for BMDELN."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from api.elabftw_client import ElabFTWClient, STATUS_RUNNING
from parsers import format_body_for_code

LOG_FILE = Path.home() / '.bmdeln' / 'job_event.log'
STATE_DIR = Path.home() / '.bmdeln' / 'jobs'


def log(message: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] JOBEVENT | {message}'
    print(line, file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as handle:
        handle.write(line + '\n')


def load_state(job_id: str):
    state_file = STATE_DIR / f'{job_id}.json'
    with open(state_file) as handle:
        state = json.load(handle)
    return state_file, state


def wait_for_state(job_id: str, attempts: int = 20, delay: float = 0.5):
    last_exc = None
    for _ in range(attempts):
        try:
            return load_state(job_id)
        except Exception as exc:
            last_exc = exc
            time.sleep(delay)
    raise last_exc


def save_state(state_file: Path, state: dict):
    with open(state_file, 'w') as handle:
        json.dump(state, handle, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description='Update BMDELN top-level job state')
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--event', choices=['start'], required=True)
    args = parser.parse_args()

    if not args.job_id:
        return 0

    try:
        state_file, state = wait_for_state(args.job_id)
    except Exception as exc:
        log(f'Could not load state for job {args.job_id}: {exc}')
        return 0

    slurm_meta = state.get('slurm_meta', {})
    current = str(slurm_meta.get('status') or '').upper()
    if current == 'RUNNING':
        return 0

    slurm_meta['status'] = 'RUNNING'
    state['slurm_meta'] = slurm_meta
    save_state(state_file, state)

    exp_id = state.get('exp_id')
    if not exp_id:
        return 0

    try:
        client = ElabFTWClient()
        body = format_body_for_code(state.get('code', 'Unknown'), state.get('input_meta', {}), slurm_meta)
        client.update_experiment(int(exp_id), body=body, status_id=STATUS_RUNNING)
        log(f'Updated experiment {exp_id} to RUNNING for job {args.job_id}')
    except Exception as exc:
        log(f'Could not update experiment {exp_id}: {exc}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
