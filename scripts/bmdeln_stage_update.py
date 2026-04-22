#!/usr/bin/env python3
"""Live Amber stage progress updater for BMDELN."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from api.elabftw_client import ElabFTWClient
from parsers import format_body_for_code

LOG_FILE = Path.home() / '.bmdeln' / 'stage_update.log'
STATE_DIR = Path.home() / '.bmdeln' / 'jobs'


def log(message: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] STAGE | {message}'
    print(line, file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a') as handle:
        handle.write(line + '\n')



def load_state(job_id: str) -> tuple[Path, dict]:
    state_file = STATE_DIR / f'{job_id}.json'
    with open(state_file) as handle:
        state = json.load(handle)
    return state_file, state



def save_state(state_file: Path, state: dict):
    with open(state_file, 'w') as handle:
        json.dump(state, handle, indent=2)



def update_stage(step: dict, event: str, exit_code: str):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if event == 'start':
        step['status'] = 'RUNNING'
        step['started_at'] = now
        return
    if exit_code == '0':
        step['status'] = 'COMPLETED'
    else:
        step['status'] = 'FAILED'
    step['finished_at'] = now
    step['exit_code'] = exit_code



def main() -> int:
    parser = argparse.ArgumentParser(description='Update BMDELN Amber stage progress')
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--stage-index', type=int, required=True)
    parser.add_argument('--event', choices=['start', 'finish'], required=True)
    parser.add_argument('--exit-code', default='0')
    args = parser.parse_args()

    try:
        state_file, state = load_state(args.job_id)
    except Exception as exc:
        log(f'Could not load state for job {args.job_id}: {exc}')
        return 0

    steps = state.get('input_meta', {}).get('amber_steps') or []
    if args.stage_index < 0 or args.stage_index >= len(steps):
        log(f'Ignoring unknown Amber stage index {args.stage_index} for job {args.job_id}')
        return 0

    update_stage(steps[args.stage_index], args.event, args.exit_code)
    save_state(state_file, state)

    exp_id = state.get('exp_id')
    if not exp_id:
        return 0

    try:
        client = ElabFTWClient()
        body = format_body_for_code(state.get('code', 'Amber'), state.get('input_meta', {}), state.get('slurm_meta', {}))
        client.update_experiment(int(exp_id), body=body)
        log(f'Updated stage {steps[args.stage_index].get("step_id")} -> {steps[args.stage_index].get("status")} for experiment {exp_id}')
    except Exception as exc:
        log(f'Could not update experiment {exp_id}: {exc}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
