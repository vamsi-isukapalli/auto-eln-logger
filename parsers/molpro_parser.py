"""
Molpro input and output file parser for BMD ELN logger.
Tested against Molpro 2024.1 output format.
"""

import re
import os
from typing import Optional


# ─────────────────────────────────────────────
# INPUT FILE PARSER
# ─────────────────────────────────────────────

def parse_molpro_input(inp_path: str) -> dict:
    """
    Parse a Molpro .inp or .com file and extract key metadata.
    Returns a flat dict of metadata fields.
    """
    metadata = {
        "code":             "Molpro",
        "input_file":       os.path.basename(inp_path),
        "title":            None,
        "memory_mw":        None,
        "basis":            None,
        "symmetry":         None,
        "geometry_file":    None,
        "geom_type":        None,
        "methods":          [],        # list of methods found (HF, DFT, CCSD(T), etc.)
        "functional":       None,      # DFT functional if used
        "charge":           None,
        "spin":             None,
        "job_type":         None,      # ENERGY / GRADIENT / FREQ / OPT
        "wf_electrons":     None,
        "wf_symmetry":      None,
        "wf_spin":          None,
        "parse_errors":     [],
    }

    try:
        with open(inp_path, "r") as f:
            raw = f.read()
    except Exception as e:
        metadata["parse_errors"].append(f"Could not read input file: {e}")
        return metadata

    # Strip comment lines (starting with !)
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("!"):
            continue
        # Remove inline comments
        cleaned = re.split(r"\s*!.*$", stripped)[0].strip()
        if cleaned:
            lines.append(cleaned)
    content = "\n".join(lines)

    def find(pattern, text=content, flags=re.IGNORECASE | re.MULTILINE):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else None

    # Title — first line starting with ***
    title_m = re.search(r"^\*\*\*,?\s*(.+)", raw, re.MULTILINE)
    if title_m:
        metadata["title"] = title_m.group(1).strip()

    # Memory
    mem = find(r"(?:^|\n)\s*memory\s*,\s*([\d.]+)\s*,\s*(\w+)", content)
    if mem:
        metadata["memory_mw"] = mem

    # Full memory line
    mem_m = re.search(r"(?:^|\n)\s*memory\s*,\s*([\d.]+)\s*,\s*(\w+)",
                      content, re.IGNORECASE | re.MULTILINE)
    if mem_m:
        val, unit = mem_m.group(1), mem_m.group(2).lower()
        metadata["memory_mw"] = f"{val} {unit}"

    # Basis set — look for DEFAULT= or BASIS block
    basis_default = find(r"DEFAULT\s*=\s*(\S+)")
    if basis_default:
        metadata["basis"] = basis_default
    else:
        # Look for basis set name in BASIS block
        basis_m = re.search(r"BASIS\s*\{?(.*?)\}?END", content,
                            re.IGNORECASE | re.DOTALL)
        if basis_m:
            metadata["basis"] = "custom (see input)"

    # Symmetry
    sym = find(r"(?:^|\n)\s*symmetry\s*,\s*(\S+)")
    metadata["symmetry"] = sym.rstrip(";").rstrip(",") if sym else "nosym"

    # Geometry
    geom_file = find(r"geometry\s*=\s*(\S+\.xyz)")
    if geom_file:
        metadata["geometry_file"] = geom_file
        metadata["geom_type"] = "xyz"
    else:
        geom_type = find(r"geomtyp\s*=\s*(\S+)")
        metadata["geom_type"] = geom_type

    # Methods — scan for method keywords
    METHOD_PATTERNS = {
        "DF-RKS/DFT":  r"\{?\s*df-rks\s*,",
        "RKS/DFT":     r"\{?\s*rks\s*,",
        "HF":          r"\{?\s*hf\s*[;\}]",
        "CCSD(T)":     r"\{?\s*ccsd\s*\(\s*t\s*\)",
        "CCSD":        r"\{?\s*ccsd\s*[;\}]",
        "MP2":         r"\{?\s*mp2\s*[;\}]",
        "CASSCF":      r"\{?\s*casscf\s*[;\}]",
        "CASPT2":      r"\{?\s*caspt2\s*[;\}]",
        "MRCI":        r"\{?\s*mrci\s*[;\}]",
        "CI":          r"\{?\s*ci\s*[;\}]",
    }
    methods_found = []
    for name, pat in METHOD_PATTERNS.items():
        if re.search(pat, content, re.IGNORECASE):
            methods_found.append(name)
    # Deduplicate — if DF-RKS found, drop plain RKS
    if "DF-RKS/DFT" in methods_found and "RKS/DFT" in methods_found:
        methods_found.remove("RKS/DFT")
    metadata["methods"] = methods_found

    # DFT functional
    func_m = re.search(r"\{?\s*df-rks\s*,\s*(\S+)", content, re.IGNORECASE)
    if not func_m:
        func_m = re.search(r"\{?\s*rks\s*,\s*(\S+)", content, re.IGNORECASE)
    if func_m:
        metadata["functional"] = func_m.group(1).strip()

    # Wavefunction: {wf, nelec, sym, spin}
    wf_m = re.search(r"wf\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
                     content, re.IGNORECASE)
    if wf_m:
        metadata["wf_electrons"] = wf_m.group(1)
        metadata["wf_symmetry"]  = wf_m.group(2)
        metadata["wf_spin"]      = wf_m.group(3)

    # Job type — detect from keywords
    if re.search(r"^\s*optg", content, re.IGNORECASE | re.MULTILINE):
        metadata["job_type"] = "GEOMETRY_OPT"
    elif re.search(r"^\s*freq", content, re.IGNORECASE | re.MULTILINE):
        metadata["job_type"] = "FREQUENCIES"
    elif re.search(r"^\s*forces", content, re.IGNORECASE | re.MULTILINE):
        metadata["job_type"] = "GRADIENT"
    else:
        metadata["job_type"] = "ENERGY"

    return metadata


# ─────────────────────────────────────────────
# OUTPUT FILE PARSER
# ─────────────────────────────────────────────

def parse_molpro_output(out_path: str) -> dict:
    """
    Parse a Molpro output file and extract key results.
    Returns a flat dict of result fields.
    """
    results = {
        "terminated_normally": False,
        "n_warnings":          0,
        "n_errors":            0,
        "molpro_version":      None,
        "final_energy_au":     None,
        "method_energies":     {},     # {method: energy}
        "homo_ev":             None,
        "lumo_ev":             None,
        "gap_ev":              None,
        "dipole":              None,
        "real_time_s":         None,
        "cpu_time_s":          None,
        "disk_used_mb":        None,
        "parse_errors":        [],
    }

    if not os.path.exists(out_path):
        results["parse_errors"].append(f"Output file not found: {out_path}")
        return results

    try:
        with open(out_path, "r") as f:
            content = f.read()
    except Exception as e:
        results["parse_errors"].append(f"Could not read output: {e}")
        return results

    # Normal termination
    term_m = re.search(
        r"Molpro calculation terminated(?: normally| with (\d+) warning)",
        content, re.IGNORECASE)
    if term_m:
        results["terminated_normally"] = True
        if term_m.group(1):
            results["n_warnings"] = int(term_m.group(1))

    # Version
    ver_m = re.search(r"Version\s+([\d.]+)", content)
    if ver_m:
        results["molpro_version"] = ver_m.group(1)

    # Method energies — lines like: !RKS STATE 1.1 Energy  -4892.652...
    # or: !CCSD(T) total energy   -622.812...
    energy_patterns = [
        (r"!RKS STATE.*?Energy\s+([+-]?\d+\.\d+)",           "DF-RKS"),
        (r"!HF STATE.*?Energy\s+([+-]?\d+\.\d+)",            "HF"),
        (r"!CCSD\(T\)\s+total energy\s+([+-]?\d+\.\d+)",     "CCSD(T)"),
        (r"!CCSD\s+total energy\s+([+-]?\d+\.\d+)",          "CCSD"),
        (r"!MP2\s+total energy\s+([+-]?\d+\.\d+)",           "MP2"),
        (r"!CASSCF\s+total energy\s+([+-]?\d+\.\d+)",        "CASSCF"),
        (r"!CASPT2\s+total energy\s+([+-]?\d+\.\d+)",        "CASPT2"),
        (r"DF-RKS/\S+\s+energy\s*=\s*([+-]?\d+\.\d+)",      "DF-RKS"),
        (r"SETTING\s+\w+\s*=\s*([+-]?\d+\.\d+)\s+AU",       "variable"),
    ]
    for pat, method in energy_patterns:
        matches = re.findall(pat, content, re.IGNORECASE)
        if matches:
            results["method_energies"][method] = float(matches[-1])

    # Final energy — last method energy found, priority order
    for method in ["CCSD(T)", "CASPT2", "CCSD", "MP2", "CASSCF", "DF-RKS", "HF"]:
        if method in results["method_energies"]:
            results["final_energy_au"] = results["method_energies"][method]
            break

    # HOMO/LUMO
    homo_m = re.search(r"HOMO\s+\S+\s+([+-]?\d+\.\d+)\s*=\s*([+-]?\d+\.\d+)eV",
                       content)
    if homo_m:
        results["homo_ev"] = float(homo_m.group(2))

    lumo_m = re.search(r"LUMO\s+\S+\s+([+-]?\d+\.\d+)\s*=\s*([+-]?\d+\.\d+)eV",
                       content)
    if lumo_m:
        results["lumo_ev"] = float(lumo_m.group(2))

    gap_m = re.search(r"LUMO-HOMO\s+([+-]?\d+\.\d+)\s*=\s*([+-]?\d+\.\d+)eV",
                      content)
    if gap_m:
        results["gap_ev"] = float(gap_m.group(2))

    # Dipole moment
    dip_m = re.search(r"Dipole moment\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)\s+([+-]?\d+\.\d+)",
                      content)
    if dip_m:
        results["dipole"] = f"{dip_m.group(1)}, {dip_m.group(2)}, {dip_m.group(3)} a.u."

    # Timing — last occurrence
    real_times = re.findall(r"REAL TIME\s+\*\s+([\d.]+)\s+SEC", content)
    if real_times:
        results["real_time_s"] = float(real_times[-1])

    cpu_times = re.findall(r"CPU TIMES\s+\*\s+([\d.]+)", content)
    if cpu_times:
        results["cpu_time_s"] = float(cpu_times[-1])

    # Disk used
    disk_m = re.search(r"DISK USED\s+\*\s+([\d.]+)\s+MB\s+\(local\)", content)
    if disk_m:
        results["disk_used_mb"] = float(disk_m.group(1))

    return results


# ─────────────────────────────────────────────
# HTML FORMATTER FOR eLabFTW
# ─────────────────────────────────────────────

def format_elabftw_body_molpro(input_meta: dict, slurm_meta: dict,
                                output_results: dict = None) -> str:
    """Format parsed Molpro metadata into HTML for eLabFTW entry."""

    def row(label, value, default="—"):
        val = value if value not in (None, [], "", {}) else default
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val) if val else default
        return f"<tr><td><b>{label}</b></td><td>{val}</td></tr>"

    html = "<h2>Job Metadata</h2>"
    html += "<table border='1' cellpadding='5' cellspacing='0'>"
    html += row("Code",           input_meta.get("code"))
    html += row("Input File",     input_meta.get("input_file"))
    html += row("Title",          input_meta.get("title"))
    html += row("Job Type",       input_meta.get("job_type"))
    html += row("Methods",        input_meta.get("methods"))
    html += row("Functional",     input_meta.get("functional"))
    html += row("Basis Set",      input_meta.get("basis"))
    html += row("Symmetry",       input_meta.get("symmetry"))
    html += row("Geometry File",  input_meta.get("geometry_file"))
    html += row("Memory",         input_meta.get("memory_mw"))
    if input_meta.get("wf_electrons"):
        html += row("WF Electrons",   input_meta.get("wf_electrons"))
        html += row("WF Spin",        input_meta.get("wf_spin"))
    html += "</table>"

    html += "<h2>Slurm Job Details</h2>"
    html += "<table border='1' cellpadding='5' cellspacing='0'>"
    html += row("Job ID",         slurm_meta.get("job_id"))
    html += row("Job Name",       slurm_meta.get("job_name"))
    html += row("User",           slurm_meta.get("user"))
    html += row("Partition",      slurm_meta.get("partition"))
    html += row("Nodes",          slurm_meta.get("nodes"))
    html += row("Tasks (MPI)",    slurm_meta.get("ntasks"))
    html += row("Requested Time", slurm_meta.get("time_limit"))
    html += row("Submit Dir",     slurm_meta.get("submit_dir"))
    html += row("Status",         slurm_meta.get("status", "SUBMITTED"))
    html += "</table>"

    if output_results:
        html += "<h2>Results</h2>"
        html += "<table border='1' cellpadding='5' cellspacing='0'>"
        html += row("Terminated Normally",
                    "✅ Yes" if output_results.get("terminated_normally") else "❌ No")
        html += row("Warnings",         output_results.get("n_warnings"))
        html += row("Molpro Version",   output_results.get("molpro_version"))
        html += row("Final Energy (a.u.)", output_results.get("final_energy_au"))

        # Method energies table
        method_e = output_results.get("method_energies", {})
        if method_e:
            energy_rows = "".join(
                f"<tr><td>{m}</td><td>{e:.10f}</td></tr>"
                for m, e in method_e.items()
            )
            html += f"<tr><td><b>Method Energies</b></td><td><table>{energy_rows}</table></td></tr>"

        if output_results.get("homo_ev"):
            html += row("HOMO (eV)",  f"{output_results['homo_ev']:.4f}")
        if output_results.get("lumo_ev"):
            html += row("LUMO (eV)",  f"{output_results['lumo_ev']:.4f}")
        if output_results.get("gap_ev"):
            html += row("HOMO-LUMO Gap (eV)", f"{output_results['gap_ev']:.4f}")
        if output_results.get("dipole"):
            html += row("Dipole Moment", output_results.get("dipole"))
        html += row("Real Time (s)",  output_results.get("real_time_s"))
        html += row("CPU Time (s)",   output_results.get("cpu_time_s"))
        html += row("Disk Used (MB)", output_results.get("disk_used_mb"))
        html += "</table>"

    if input_meta.get("parse_errors"):
        html += f"<p style='color:orange'>⚠️ Parser warnings: {'; '.join(input_meta['parse_errors'])}</p>"

    return html


if __name__ == "__main__":
    import sys, json
    inp = sys.argv[1] if len(sys.argv) > 1 else "h5o2p_c1_z_pbe0_harm_2.inp"
    out = sys.argv[2] if len(sys.argv) > 2 else inp.replace(".inp", ".out").replace(".com", ".out")
    print("=== INPUT ===")
    print(json.dumps(parse_molpro_input(inp), indent=2))
    print("=== OUTPUT ===")
    print(json.dumps(parse_molpro_output(out), indent=2))
