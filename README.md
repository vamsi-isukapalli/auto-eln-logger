# BMDELN — BMD eLabFTW Logger

BMDELN is a workflow-aware Slurm logger that creates and updates eLabFTW entries for jobs submitted on **BMDCluster**, while the eLabFTW server itself runs on **BMDPC2**.

The current design supports:

- standard single-job submissions
- sequential multi-step workflows inside one Slurm script
- Amber-style chained stage jobs
- script-driven loop workflows
- Slurm array workflows
- native eLabFTW status tracking (`Queued`, `Running`, `Success`, `Need to be redone`, `Fail`)
- automatic upload of selected job files and cluster-path references for large files
- hardware metadata logging for benchmarking

## Architecture

- **BMDPC2**: hosts the eLabFTW server and database
- **BMDCluster**: runs `bmdsubmit`, Slurm jobs, epilog logic, parsers, and state tracking
- **User local PC**: only needed for browser access to eLabFTW

Server-side Docker/MySQL credentials remain only on **BMDPC2**. Cluster users only need the BMDELN client code plus their own eLabFTW API token.

## Main Components

- `bmdsubmit.py` — wrapper around `sbatch`
- `parsers/workflow_detector.py` — inspects submit scripts and detects job structure
- `parsers/code_detector.py` — identifies the quantum chemistry / simulation code
- code parsers:
  - `cp2k_parser.py`
  - `molpro_parser.py`
  - `orca_parser.py`
  - `amber_parser.py`
  - `openmolcas_parser.py`
- `scripts/bmdeln_epilog.py` — final job update, file upload, and summary
- `scripts/bmdeln_job_event.py` — runtime status transitions such as `Queued -> Running`
- `api/elabftw_client.py` — eLabFTW API client

## What BMDELN Logs

Depending on job type, BMDELN can log:

- code and run type
- project tag
- Slurm metadata
- hardware metadata (node, CPU model, sockets, cores, memory)
- selected input / output / Slurm files
- workflow stages or array progress
- parser-derived scientific metadata and results

## eLabFTW Status Model

BMDELN expects the following experiment statuses to exist in eLabFTW:

1. `Running`
2. `Success`
3. `Need to be redone`
4. `Fail`
5. `Queued`

The logger uses these statuses automatically:

- `Queued` immediately after `sbatch` succeeds
- `Running` when the job actually starts on a node
- `Success` on successful completion
- `Fail` on runtime failure after start
- `Need to be redone` for parser-incomplete / manual-review cases

## Basic Usage

### Single-job mode

```bash
bmdsubmit input.inp submit.sh
```

### Project-tagged submission

```bash
bmdsubmit --project WATER-PROJECT input.inp submit.sh
```

### Workflow / script-only mode

Use this when the submit script itself determines the real inputs and outputs.

```bash
bmdsubmit submit.sh
```

This is the preferred mode for:

- loop workflows
- scripts that move across many directories
- Slurm arrays
- scripts where the real input file names are generated or chosen internally

### Help

```bash
bmdsubmit --help
```

Running `bmdsubmit` with no arguments also prints usage instructions.

## Supported Workflow Patterns

### 1. Standard single job
One submit script runs one main calculation.

### 2. Sequential multi-step job
Example: Amber equilibration with `equi_01`, `equi_02`, `equi_03`, `equi_04` inside one Slurm script.

### 3. Loop workflow
Example: one submitter loops through many directories and runs one calculation per directory.

### 4. Array workflow
Example: `#SBATCH --array=1-663` with task-specific folder names inferred from a mapping file or shell logic.

## File Upload Policy

BMDELN tries to avoid flooding entries with unrelated files.

### Always upload when available

- the main input file or submit script
- the main output/log file
- the Slurm stdout/stderr file

### Upload only when small and job-specific

- coordinate files
- small restart files
- selected auxiliary text files

### Do not blindly upload

- unrelated files in the same directory
- large trajectory or binary files
- broad `*.xyz` directory sweeps

Large files are referenced in the ELN entry by cluster path instead of being uploaded.

## Current Known Limitations

- pending-job cancellation before job start is **not** reconciled automatically
- highly dynamic Bash workflows may still need conventions or future detector improvements
- array / workflow handling is summary-oriented and aims for **one ELN entry per overall workflow**, not one entry per subtask

## Recommended Testing Order

1. `bmdsubmit --help`
2. one CP2K single job
3. one Molpro job
4. one Amber multi-step job
5. one script-only loop workflow
6. one script-only array workflow

## Repo Notes

This codebase is the **client-side logger**. The eLabFTW server deployment is separate and stays on BMDPC2.
