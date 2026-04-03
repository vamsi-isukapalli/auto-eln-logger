#!/usr/bin/env python3
"""
bmdeln_epilog.py — runs at the END of every BMD Slurm job.

Injected automatically by bmdsubmit into the job script.
Reads environment variables set by bmdsubmit, parses the output file,
and updates the eLabFTW entry with results.

Environment variables expected:
    BMDELN_EXP_ID       — eLabFTW experiment ID
    BMDELN_CODE         — QC code (CP2K, ORCA, etc.)
    BMDELN_INPUT_FILE   — full path to input file
    BMDELN_OUTPUT_FILE  — full path to output/log file
    BMDELN_SUBMIT_DIR   — original submission directory
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent.parent  # bmdeln root
sys.path.insert(0, str(SCRIPT_DIR))

from parsers.cp2k_parser  import parse_cp2k_input, parse_cp2k_output, format_elabftw_body
from api.elabftw_client   import ElabFTWClient, STATUS_SUCCESS, STATUS_FAIL

LOG_FILE  = Path.home() / ".bmdeln" / "epilog.log"
STATE_DIR = Path.home() / ".bmdeln" / "jobs"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] EPILOG | {msg}"
    print(line, file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def main():
    exp_id_str   = os.environ.get("BMDELN_EXP_ID", "")
    code         = os.environ.get("BMDELN_CODE", "Unknown")
    input_file   = os.environ.get("BMDELN_INPUT_FILE", "")
    output_file  = os.environ.get("BMDELN_OUTPUT_FILE", "")
    submit_dir   = os.environ.get("BMDELN_SUBMIT_DIR", "")
    slurm_job_id = os.environ.get("SLURM_JOB_ID", "unknown")

    log(f"Starting epilog for job {slurm_job_id}, eLabFTW exp={exp_id_str}, code={code}")

    if not exp_id_str:
        log("No BMDELN_EXP_ID set — skipping eLabFTW update.")
        return

    exp_id = int(exp_id_str)
    client = ElabFTWClient()

    # ── Load saved state (input_meta, slurm_meta) ────────────────────────────
    state_file = STATE_DIR / f"{slurm_job_id}.json"
    input_meta = {}
    slurm_meta = {}
    if state_file.exists():
        with open(state_file) as f:
            state = json.load(f)
        input_meta = state.get("input_meta", {})
        slurm_meta = state.get("slurm_meta", {})
    else:
        log(f"State file not found: {state_file}. Re-parsing input.")
        if code == "CP2K" and input_file:
            input_meta = parse_cp2k_input(input_file)

    # ── Parse output file ─────────────────────────────────────────────────────
    output_results = None
    if code == "CP2K":
        log(f"Parsing CP2K output: {output_file}")
        output_results = parse_cp2k_output(output_file)
        terminated_ok = output_results.get("terminated_normally", False)
        log(f"Terminated normally: {terminated_ok}, "
            f"Energy: {output_results.get('final_energy_au')}")
    else:
        log(f"No output parser for {code} — updating status only.")
        terminated_ok = True  # assume ok; no parser

    # ── Determine job success ─────────────────────────────────────────────────
    # For CP2K: rely on output parser only — SLURM_JOB_STATE not available
    # in injected epilog scripts
    job_success = terminated_ok

    # ── Update slurm_meta with final status ───────────────────────────────────
    slurm_meta["status"] = "COMPLETED" if job_success else "FAILED (see output)"
    slurm_meta["actual_walltime"] = os.environ.get("SLURM_JOB_ELAPSED", "—")

    # ── Build updated body ────────────────────────────────────────────────────
    try:
        if code == "CP2K" and input_meta:
            updated_body = format_elabftw_body(input_meta, slurm_meta, output_results)
        else:
            status_color = "green" if job_success else "red"
            status_icon  = "✅" if job_success else "❌"
            updated_body = (
                f"<p style='color:{status_color}'>{status_icon} "
                f"Job {slurm_job_id} finished with status: {slurm_meta['status']}</p>"
            )
            if code != "CP2K":
                updated_body += (
                    f"<p style='color:red'>⚠️ <b>Non-CP2K job ({code})</b>. "
                    f"Output parsing not yet implemented. "
                    f"Please annotate results manually.</p>"
                )

        client.update_experiment(
            exp_id,
            body=updated_body,
            status_id=STATUS_SUCCESS if job_success else STATUS_FAIL,
        )
        log(f"Updated eLabFTW entry {exp_id} — status: {'SUCCESS' if job_success else 'FAIL'}")
    except Exception as e:
        log(f"ERROR updating eLabFTW entry: {e}")

    # ── Smart file handling ───────────────────────────────────────────────────
    # Only upload small files (< MAX_UPLOAD_MB) to eLabFTW.
    # Large files are referenced by cluster path only — never uploaded.
    MAX_UPLOAD_MB = 10

    def file_size_mb(path):
        try:
            return Path(path).stat().st_size / (1024 * 1024)
        except Exception:
            return 0.0

    files_to_upload  = []   # (path, comment) small files only
    large_file_paths = []   # (path, label)   cluster paths only

    # Input file — always small, always upload
    if input_file and Path(input_file).exists():
        files_to_upload.append((input_file, "CP2K input file"))

    # Coord file — upload only if small
    coord_file = input_meta.get("coord_file")
    if coord_file:
        coord_path = Path(submit_dir) / coord_file
        if coord_path.exists():
            if file_size_mb(str(coord_path)) <= MAX_UPLOAD_MB:
                files_to_upload.append((str(coord_path), "Coordinate file (XYZ)"))
            else:
                large_file_paths.append((str(coord_path),
                    f"Coordinate file ({file_size_mb(str(coord_path)):.1f} MB — access on cluster)"))

    # Output log — reference by path only if large
    if output_file:
        size_mb = file_size_mb(output_file)
        if Path(output_file).exists():
            if size_mb <= MAX_UPLOAD_MB:
                files_to_upload.append((output_file, f"CP2K output log ({size_mb:.1f} MB)"))
            else:
                large_file_paths.append((output_file,
                    f"CP2K output log ({size_mb:.1f} MB — too large, access on cluster)"))
        else:
            large_file_paths.append((output_file, "CP2K output log (not found)"))

    # Trajectory files — always reference by path, never upload
    for pattern in ["*-pos-1.xyz", "*TRAJ*", "*.dcd", "*.xtc"]:
        for traj in Path(submit_dir).glob(pattern):
            if traj.name != coord_file:
                large_file_paths.append((str(traj),
                    f"Trajectory ({file_size_mb(str(traj)):.1f} MB — access on cluster)"))

    # Upload small files
    for fpath, comment in files_to_upload:
        try:
            client.upload_file(exp_id, fpath, comment=comment)
            log(f"Uploaded: {fpath}")
        except Exception as e:
            log(f"WARNING: Could not upload {fpath}: {e}")

    # Append large file paths as a reference table in the ELN entry body
    if large_file_paths:
        path_html = "<h2>Data Files on Cluster</h2>"
        path_html += "<p><i>Files too large to upload — access directly on bmdcluster.</i></p>"
        path_html += "<table border='1' cellpadding='5' cellspacing='0'>"
        path_html += "<tr><th>File</th><th>Path on bmdcluster</th></tr>"
        for fpath, label in large_file_paths:
            path_html += f"<tr><td>{label}</td><td><code>{fpath}</code></td></tr>"
        path_html += "</table>"
        try:
            current = client._req("get", f"/experiments/{exp_id}").json()
            current_body = current.get("body", "")
            client.update_experiment(exp_id, body=current_body + path_html)
            log(f"Added {len(large_file_paths)} large file path(s) to ELN entry")
        except Exception as e:
            log(f"WARNING: Could not append file paths: {e}")

    # ── Add completion comment ─────────────────────────────────────────────────
    try:
        result_summary = ""
        if output_results and output_results.get("final_energy_au"):
            result_summary = (f" | Final energy: "
                              f"{output_results['final_energy_au']:.6f} a.u.")
        client.add_comment(
            exp_id,
            f"Job {slurm_job_id} completed at "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            f" — Status: {slurm_meta['status']}{result_summary}"
        )
    except Exception as e:
        log(f"WARNING: Could not add comment: {e}")

    # ── Clean up state file ───────────────────────────────────────────────────
    try:
        if state_file.exists():
            state_file.unlink()
    except Exception:
        pass

    log("Epilog complete.")


if __name__ == "__main__":
    main()
