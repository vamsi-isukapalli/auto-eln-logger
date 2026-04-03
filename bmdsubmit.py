#!/usr/bin/env python3
"""
bmdsubmit — BMD Group ELN-aware Slurm job submitter.

Usage:
    bmdsubmit <input_file.inp> [sbatch_script.sh] [-- extra sbatch args]

Examples:
    bmdsubmit H2O_64.inp                         # auto-finds submit.sh in cwd
    bmdsubmit H2O_64.inp submit_cp2k.sh          # explicit submit script
    bmdsubmit H2O_64.inp submit_cp2k.sh -- --partition=p8468

What it does:
    1. Detects QC code from job script
    2. Parses CP2K input file (warns for other codes)
    3. Creates eLabFTW entry (status: SUBMITTED)
    4. Injects epilog call into a temp copy of the job script
    5. Submits to Slurm via real sbatch
    6. Updates eLabFTW entry with Slurm Job ID

The injected epilog calls `bmdeln_epilog` at job end, which:
    - Parses the output file
    - Updates eLabFTW with results and status
    - Uploads input + output files
"""

import os
import sys
import re
import json
import shutil
import tempfile
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

# ── Path setup ───────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
PARSERS_DIR = SCRIPT_DIR / "parsers"
API_DIR     = SCRIPT_DIR / "api"
sys.path.insert(0, str(SCRIPT_DIR))

from parsers.cp2k_parser   import parse_cp2k_input, format_elabftw_body
from parsers.code_detector import detect_code
from api.elabftw_client    import ElabFTWClient

# ── Config ───────────────────────────────────────────────────────────────────
REAL_SBATCH     = "/usr/bin/sbatch"     # path to real sbatch binary
EPILOG_SCRIPT   = str(SCRIPT_DIR / "scripts" / "bmdeln_epilog.py")
STATE_DIR       = Path.home() / ".bmdeln" / "jobs"  # stores per-job state JSON
LOG_FILE        = Path.home() / ".bmdeln" / "bmdsubmit.log"

# ── Logging ──────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Slurm script helpers ──────────────────────────────────────────────────────

def parse_sbatch_directives(script_content: str) -> dict:
    """Extract #SBATCH directives from a job script."""
    directives = {}
    for line in script_content.splitlines():
        m = re.match(r"^#SBATCH\s+--(\w[\w-]*)\s*=?\s*(.+)", line.strip())
        if m:
            key = m.group(1).replace("-", "_")
            directives[key] = m.group(2).strip()
    return directives


def find_output_file(submit_dir: str, input_file: str,
                     sbatch_directives: dict) -> str:
    """
    Guess the output log file path from CP2K convention.
    CP2K log is typically: <input_basename>.log or H2O_64.log
    """
    base = Path(input_file).stem
    candidates = [
        f"{base}.log",
        f"{base}.out",
        sbatch_directives.get("output", ""),
    ]
    for c in candidates:
        if c:
            full = Path(submit_dir) / c
            if full.exists():
                return str(full)
    # Return the most likely even if not yet existing (job hasn't run)
    return str(Path(submit_dir) / f"{base}.log")


def inject_epilog(script_content: str, epilog_cmd: str) -> str:
    """
    Inject the epilog command at the very end of the job script,
    before any final 'echo' or after the main execution line.
    """
    lines = script_content.splitlines()
    # Find the last non-empty, non-echo line and insert after it
    insert_at = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line and not line.startswith("#"):
            insert_at = i + 1
            break

    lines.insert(insert_at, f"\n# ── BMD ELN epilog (auto-injected) ──")
    lines.insert(insert_at + 1, epilog_cmd)
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BMD ELN-aware Slurm job submitter")
    parser.add_argument("input_file",
        help="QC input file (e.g. H2O_64.inp)")
    parser.add_argument("submit_script", nargs="?", default=None,
        help="Slurm submit script (default: auto-detect *.sh in cwd)")
    parser.add_argument("extra_args", nargs=argparse.REMAINDER,
        help="Extra args passed to sbatch (after --)")
    args = parser.parse_args()

    submit_dir  = str(Path.cwd())
    input_file  = args.input_file
    input_path  = str(Path(submit_dir) / input_file)

    # ── Find submit script ────────────────────────────────────────────────────
    submit_script = args.submit_script
    if not submit_script:
        sh_files = list(Path(submit_dir).glob("*.sh"))
        if len(sh_files) == 1:
            submit_script = str(sh_files[0])
            log(f"Auto-detected submit script: {submit_script}")
        else:
            log("ERROR: Could not auto-detect submit script. "
                "Please pass it explicitly: bmdsubmit <input> <script.sh>")
            sys.exit(1)

    with open(submit_script, "r") as f:
        script_content = f.read()

    # ── Detect QC code ────────────────────────────────────────────────────────
    detected_code = detect_code(script_content)
    log(f"Detected QC code: {detected_code}")

    # ── Parse input metadata ──────────────────────────────────────────────────
    if detected_code == "CP2K":
        input_meta = parse_cp2k_input(input_path)
        log(f"Parsed CP2K input: project={input_meta.get('project_name')}, "
            f"run_type={input_meta.get('run_type')}")
    else:
        log(f"WARNING: Non-CP2K job detected ({detected_code}). "
            f"Partial logging only.")
        input_meta = {
            "code": detected_code,
            "input_file": input_file,
            "project_name": None,
            "run_type": None,
            "functional": None,
            "parse_errors": [f"Parser not yet implemented for {detected_code}"],
        }

    # ── Parse Slurm directives ────────────────────────────────────────────────
    directives = parse_sbatch_directives(script_content)
    slurm_meta = {
        "job_name":   directives.get("job_name", Path(submit_dir).name).split()[0],  # strip inline comments
        "partition":  directives.get("partition", "unknown").split()[0],  # strip inline comments
        "nodes":      directives.get("nodes", "?"),
        "ntasks":     directives.get("ntasks", "?").split()[0],
        "time_limit": directives.get("time", "?").split()[0],
        "user":       os.environ.get("USER", "unknown"),
        "submit_dir": submit_dir,
        "status":     "SUBMITTED",
        "job_id":     None,
    }

    # ── Create eLabFTW entry ──────────────────────────────────────────────────
    client = ElabFTWClient()

    title = (f"[{detected_code}] {input_meta.get('project_name') or input_file}"
             f" — {slurm_meta['user']} — "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")

    tags = [detected_code, slurm_meta["user"], slurm_meta["partition"]]
    if input_meta.get("run_type"):
        tags.append(input_meta["run_type"])

    try:
        if detected_code == "CP2K":
            body = format_elabftw_body(input_meta, slurm_meta)
        else:
            body = (f"<p style='color:red'>⚠️ <b>Non-CP2K job detected "
                    f"({detected_code})</b>. Full metadata parsing is not yet "
                    f"supported for this code. Manual annotation required.</p>"
                    f"<p><b>Input file:</b> {input_file}<br>"
                    f"<b>Submit dir:</b> {submit_dir}</p>")

        exp_id = client.create_experiment(title=title, body=body, tags=tags)
        log(f"Created eLabFTW entry: ID={exp_id}")
    except Exception as e:
        log(f"WARNING: Could not create eLabFTW entry: {e}")
        exp_id = None

    # ── Build epilog command ──────────────────────────────────────────────────
    output_log = find_output_file(submit_dir, input_file, directives)
    epilog_env = {
        "BMDELN_EXP_ID":     str(exp_id) if exp_id else "",
        "BMDELN_CODE":        detected_code,
        "BMDELN_INPUT_FILE":  input_path,
        "BMDELN_OUTPUT_FILE": output_log,
        "BMDELN_SUBMIT_DIR":  submit_dir,
    }
    env_exports = " ".join(f'{k}="{v}"' for k, v in epilog_env.items())
    epilog_cmd  = f"{env_exports} python3 {EPILOG_SCRIPT}"

    # ── Inject epilog into a temp copy of the submit script ──────────────────
    modified_script = inject_epilog(script_content, epilog_cmd)
    tmp_script = tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False,
        dir=submit_dir, prefix=".bmdeln_tmp_")
    tmp_script.write(modified_script)
    tmp_script.close()
    os.chmod(tmp_script.name, 0o755)

    # ── Submit to real Slurm ──────────────────────────────────────────────────
    extra = [a for a in args.extra_args if a != "--"]
    cmd = [REAL_SBATCH, tmp_script.name] + extra
    log(f"Submitting: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout, end="")  # pass through to user
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

        # Parse Slurm job ID from sbatch output: "Submitted batch job 12345"
        m = re.search(r"Submitted batch job (\d+)", result.stdout)
        slurm_job_id = m.group(1) if m else None

        if slurm_job_id and exp_id:
            # Update eLabFTW entry with Slurm job ID and link to output log
            updated_body = format_elabftw_body(
                input_meta,
                {**slurm_meta, "job_id": slurm_job_id, "status": "RUNNING"},
            ) if detected_code == "CP2K" else body
            client.update_experiment(exp_id, body=updated_body)
            log(f"Updated eLabFTW entry {exp_id} with Slurm Job ID {slurm_job_id}")

            # Save state for epilog
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            state = {
                "exp_id":       exp_id,
                "slurm_job_id": slurm_job_id,
                "code":         detected_code,
                "input_file":   input_path,
                "output_file":  output_log,
                "submit_dir":   submit_dir,
                "input_meta":   input_meta,
                "slurm_meta":   {**slurm_meta, "job_id": slurm_job_id},
            }
            state_file = STATE_DIR / f"{slurm_job_id}.json"
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
            log(f"State saved: {state_file}")

        return result.returncode

    finally:
        # Clean up temp script
        try:
            os.unlink(tmp_script.name)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
