"""
CP2K input and output file parser for BMD ELN logger.
Extracts key metadata from CP2K .inp files and results from .log files.
"""

import re
import os
from typing import Optional


# ─────────────────────────────────────────────
# INPUT FILE PARSER
# ─────────────────────────────────────────────

def parse_cp2k_input(inp_path: str) -> dict:
    """
    Parse a CP2K input file and extract key metadata.
    Returns a flat dict of metadata fields.
    """
    metadata = {
        "code":             "CP2K",
        "input_file":       os.path.basename(inp_path),
        "project_name":     None,
        "run_type":         None,
        "method":           "QS/DFT",
        "functional":       None,
        "basis_set_H":      None,
        "basis_set_O":      None,
        "basis_set_other":  [],
        "cutoff":           None,
        "rel_cutoff":       None,
        "charge":           None,
        "multiplicity":     None,
        "cell_abc":         None,
        "coord_file":       None,
        "vdw_correction":   None,
        "hfx_fraction":     None,
        "scf_guess":        None,
        "md_ensemble":      None,
        "md_steps":         None,
        "md_timestep_fs":   None,
        "md_temperature_K": None,
        "geo_opt_optimizer":None,
        "geo_opt_max_iter": None,
        "admm":             False,
        "print_level":      None,
        "parse_errors":     [],
    }

    try:
        with open(inp_path, "r") as f:
            raw = f.read()
    except Exception as e:
        metadata["parse_errors"].append(f"Could not read input file: {e}")
        return metadata

    # Strip comments:
    #   - whole-line comments: lines where first non-space char is # or !
    #   - inline comments: everything after first standalone # or !
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in ("#", "!"):
            continue  # whole-line comment — skip entirely
        # Remove inline comments (! or #) but only if preceded by whitespace
        # so we don't mangle values like 1.0E-12
        cleaned = re.sub(r"\s+[#!].*$", "", stripped).strip()
        if cleaned:
            lines.append(cleaned)
    content = "\n".join(lines)

    def find(pattern, text=content, flags=re.IGNORECASE | re.MULTILINE):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else None

    # &GLOBAL
    metadata["project_name"] = find(r"(?:^|\n)\s*PROJECT\s+(\S+)")
    metadata["run_type"]     = find(r"(?:^|\n)\s*RUN_TYPE\s+(\S+)")
    metadata["print_level"]  = find(r"(?:^|\n)\s*PRINT_LEVEL\s+(\S+)")

    # &DFT — use CUTOFF but not CUTOFF_RADIUS or REL_CUTOFF
    metadata["cutoff"]      = find(r"(?:^|\n)\s*CUTOFF\s+([\d.]+)")
    metadata["rel_cutoff"]  = find(r"(?:^|\n)\s*REL_CUTOFF\s+([\d.]+)")
    metadata["scf_guess"]   = find(r"(?:^|\n)\s*SCF_GUESS\s+(\S+)")
    metadata["charge"]      = find(r"(?:^|\n)\s*CHARGE\s+([+-]?[0-9]+)")
    metadata["multiplicity"] = find(r"(?:^|\n)\s*MULTIPLICITY\s+(\d+)")

    # Functional — look for active (non-commented) &XC_FUNCTIONAL block
    func_block = re.search(r"&XC_FUNCTIONAL(.*?)&END XC_FUNCTIONAL", content,
                           re.IGNORECASE | re.DOTALL)
    if func_block:
        fb = func_block.group(1)
        # find &<FUNCTIONAL_NAME> blocks inside
        funcs = re.findall(r"&(\w+)\s*\n", fb)
        funcs = [f for f in funcs if f.upper() not in ("END",)]
        if funcs:
            metadata["functional"] = ", ".join(funcs)

    # HFX fraction
    hfx = find(r"FRACTION\s+([\d.]+)")
    if hfx:
        metadata["hfx_fraction"] = hfx

    # VdW
    vdw = find(r"TYPE\s+(DFTD\w+)")
    if vdw:
        metadata["vdw_correction"] = vdw

    # ADMM
    if re.search(r"&AUXILIARY_DENSITY_MATRIX_METHOD", content, re.IGNORECASE):
        metadata["admm"] = True

    # &SUBSYS
    cell = find(r"ABC\s+([\d.\s]+)")
    if cell:
        metadata["cell_abc"] = cell.strip()

    coord = find(r"COORD_FILE_NAME\s+(\S+)")
    if coord:
        metadata["coord_file"] = coord

    # Basis sets per kind
    # Find all &KIND blocks
    kind_blocks = re.findall(r"&KIND\s+(\w+)(.*?)&END KIND", content,
                              re.IGNORECASE | re.DOTALL)
    kind_basis = {}
    for kind_name, kind_body in kind_blocks:
        # get primary BASIS_SET (first one, not AUX_FIT)
        bs_matches = re.findall(r"BASIS_SET\s+(?!AUX_FIT)(\S+)", kind_body, re.IGNORECASE)
        if bs_matches:
            kind_basis[kind_name.upper()] = bs_matches[0]

    metadata["basis_set_H"] = kind_basis.get("H")
    metadata["basis_set_O"] = kind_basis.get("O")
    other = {k: v for k, v in kind_basis.items() if k not in ("H", "O")}
    if other:
        metadata["basis_set_other"] = [f"{k}:{v}" for k, v in other.items()]

    # &MD
    metadata["md_ensemble"]      = find(r"(?:^|\n)\s*ENSEMBLE\s+(\S+)")
    metadata["md_steps"]         = find(r"(?:^|\n)\s*STEPS\s+(\d+)")
    metadata["md_timestep_fs"]   = find(r"TIMESTEP\s+\[fs\]\s+([\d.]+)")
    metadata["md_temperature_K"] = find(r"TEMPERATURE\s+\[K\]\s+([\d.]+)")

    # &GEO_OPT
    metadata["geo_opt_optimizer"] = find(r"(?:^|\n)\s*OPTIMIZER\s+(\S+)")
    metadata["geo_opt_max_iter"]  = find(r"(?:^|\n)\s*MAX_ITER\s+(\d+)")

    return metadata


# ─────────────────────────────────────────────
# OUTPUT FILE PARSER
# ─────────────────────────────────────────────

def parse_cp2k_output(log_path: str) -> dict:
    """
    Parse a CP2K output/log file and extract key results.
    Returns a flat dict of result fields.
    """
    results = {
        "terminated_normally": False,
        "final_energy_au":     None,
        "scf_converged":       None,
        "total_time_s":        None,
        "cp2k_version":        None,
        "md_steps_completed":  None,
        "warnings":            [],
        "parse_errors":        [],
    }

    if not os.path.exists(log_path):
        results["parse_errors"].append(f"Output file not found: {log_path}")
        return results

    try:
        with open(log_path, "r") as f:
            content = f.read()
    except Exception as e:
        results["parse_errors"].append(f"Could not read output file: {e}")
        return results

    # Normal termination
    if re.search(r"CP2K\s+CONTROLLER\s+WORKER\s+STEP\s+DONE", content) or \
       re.search(r"T I M I N G", content):
        results["terminated_normally"] = True

    # CP2K version
    v = re.search(r"CP2K\s+version\s+([\d.]+)", content, re.IGNORECASE)
    if v:
        results["cp2k_version"] = v.group(1)

    # Final energy — last occurrence of total energy line
    energies = re.findall(
        r"ENERGY\|\s+Total FORCE_EVAL.*?=\s*([+-]?\d+\.\d+)", content)
    if energies:
        results["final_energy_au"] = float(energies[-1])

    # SCF convergence — look for the convergence line
    scf_conv = re.findall(r"SCF run converged in\s+(\d+)\s+iterations", content)
    if scf_conv:
        results["scf_converged"] = True
    elif re.search(r"SCF run NOT converged", content):
        results["scf_converged"] = False

    # Total time
    t = re.search(r"CP2K\s+\d+\s+\d+\.\d+\s+(\d+\.\d+)", content)
    if not t:
        # alternative: look at DBCSR STATISTICS
        t = re.search(r"Total program time.*?:\s*([\d.]+)", content)
    if t:
        results["total_time_s"] = float(t.group(1))

    # MD steps completed
    md_steps = re.findall(r"^\s*MD_INI\|.*?Step number\s+(\d+)", content,
                           re.MULTILINE)
    if md_steps:
        results["md_steps_completed"] = int(md_steps[-1])

    # Warnings
    # Filter out known non-critical HFX/SCF warnings
    IGNORE_WARN = ["Kohn Sham matrix", "hfx_energy_potential", "thermostat_methods"]
    warns = re.findall(r"\*\*\*\s+WARNING.*", content)
    warns = [w for w in warns if not any(ig in w for ig in IGNORE_WARN)]
    results["warnings"] = warns[:10]  # cap at 10

    return results


# ─────────────────────────────────────────────
# SUMMARY FORMATTER
# ─────────────────────────────────────────────

def format_elabftw_body(input_meta: dict, slurm_meta: dict,
                         output_results: dict = None) -> str:
    """
    Format parsed metadata into an HTML body for eLabFTW entry.
    """
    def row(label, value, default="—"):
        val = value if value not in (None, [], "") else default
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val) if val else default
        return f"<tr><td><b>{label}</b></td><td>{val}</td></tr>"

    html = "<h2>Job Metadata</h2>"
    html += "<table border='1' cellpadding='5' cellspacing='0'>"
    html += row("Code",            input_meta.get("code"))
    html += row("Input File",      input_meta.get("input_file"))
    html += row("Project Name",    input_meta.get("project_name"))
    html += row("Run Type",        input_meta.get("run_type"))
    html += row("Functional",      input_meta.get("functional"))
    html += row("HFX Fraction",    input_meta.get("hfx_fraction"))
    html += row("VdW Correction",  input_meta.get("vdw_correction"))
    html += row("ADMM",            "Yes" if input_meta.get("admm") else "No")
    html += row("Basis Set (H)",   input_meta.get("basis_set_H"))
    html += row("Basis Set (O)",   input_meta.get("basis_set_O"))
    html += row("Cutoff (Ry)",     input_meta.get("cutoff"))
    html += row("Rel. Cutoff",     input_meta.get("rel_cutoff"))
    html += row("Cell ABC (Å)",    input_meta.get("cell_abc"))
    html += row("Coord File",      input_meta.get("coord_file"))
    html += row("Charge",          input_meta.get("charge"))
    html += row("Multiplicity",    input_meta.get("multiplicity"))
    html += row("SCF Guess",       input_meta.get("scf_guess"))
    html += "</table>"

    html += "<h2>Slurm Job Details</h2>"
    html += "<table border='1' cellpadding='5' cellspacing='0'>"
    html += row("Job ID",          slurm_meta.get("job_id"))
    html += row("Job Name",        slurm_meta.get("job_name"))
    html += row("User",            slurm_meta.get("user"))
    html += row("Partition",       slurm_meta.get("partition"))
    html += row("Nodes",           slurm_meta.get("nodes"))
    html += row("Tasks (MPI)",     slurm_meta.get("ntasks"))
    html += row("Requested Time",  slurm_meta.get("time_limit"))
    html += row("Submit Dir",      slurm_meta.get("submit_dir"))
    html += row("Status",          slurm_meta.get("status", "SUBMITTED"))
    html += "</table>"

    if input_meta.get("run_type", "").upper() == "MD":
        html += "<h2>MD Settings</h2>"
        html += "<table border='1' cellpadding='5' cellspacing='0'>"
        html += row("Ensemble",        input_meta.get("md_ensemble"))
        html += row("Steps",           input_meta.get("md_steps"))
        html += row("Timestep (fs)",   input_meta.get("md_timestep_fs"))
        html += row("Temperature (K)", input_meta.get("md_temperature_K"))
        html += "</table>"

    if input_meta.get("run_type", "").upper() in ("GEO_OPT", "CELL_OPT"):
        html += "<h2>Geometry Optimization Settings</h2>"
        html += "<table border='1' cellpadding='5' cellspacing='0'>"
        html += row("Optimizer",  input_meta.get("geo_opt_optimizer"))
        html += row("Max Iter",   input_meta.get("geo_opt_max_iter"))
        html += "</table>"

    if output_results:
        html += "<h2>Results</h2>"
        html += "<table border='1' cellpadding='5' cellspacing='0'>"
        html += row("Terminated Normally", "✅ Yes" if output_results.get("terminated_normally") else "❌ No")
        html += row("Final Energy (a.u.)", output_results.get("final_energy_au"))
        html += row("SCF Converged",       output_results.get("scf_converged"))
        html += row("Total Time (s)",      output_results.get("total_time_s"))
        html += row("CP2K Version",        output_results.get("cp2k_version"))
        if input_meta.get("run_type", "").upper() == "MD":
            html += row("MD Steps Completed", output_results.get("md_steps_completed"))
        if output_results.get("warnings"):
            html += row("Warnings", "<br>".join(output_results["warnings"]))
        html += "</table>"

    if input_meta.get("parse_errors"):
        html += f"<p style='color:orange'>⚠️ Parser warnings: {'; '.join(input_meta['parse_errors'])}</p>"

    return html


if __name__ == "__main__":
    import sys, json
    inp = sys.argv[1] if len(sys.argv) > 1 else "H2O_64.inp"
    meta = parse_cp2k_input(inp)
    print(json.dumps(meta, indent=2))
