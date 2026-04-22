# BMDELN Version 3

## Summary

Version 3 introduces **workflow-aware job detection** so that BMDELN can handle more than the old one-input / one-output model.

This version is intended to support:

- normal single jobs
- multi-step jobs inside one Slurm script
- loop workflows
- Slurm arrays
- script-only submission where the real inputs are discovered from the submitter

## Main Additions

### 1. Workflow detector

New file:

- `parsers/workflow_detector.py`

This layer reads the submit script and tries to infer:

- workflow type
- whether the job is single, looped, array-based, or multi-stage
- what the real logical steps are
- where the effective inputs and outputs are defined

### 2. Script-only submission support

`bmdsubmit` no longer assumes that a single explicit `.inp` file is always meaningful.

You can now use:

```bash
bmdsubmit submit.sh
```

for workflows where the submitter decides the real inputs and outputs.

### 3. Better CLI help

- `bmdsubmit --help` prints clear usage instructions
- running `bmdsubmit` with no arguments also prints the help text

### 4. Molpro parser fix

Molpro success detection was too strict.

Version 3 accepts:

- `Molpro calculation terminated`

as a valid successful termination marker, instead of requiring `terminated normally`.

### 5. Status-card behavior retained

Version 3 keeps the working native eLabFTW status-card logic:

- `Queued`
- `Running`
- `Success`
- `Need to be redone`
- `Fail`

and does not clobber category.

### 6. Version 2 improvements retained

Version 3 builds on the accepted Version 2 behavior:

- CP2K result extraction improvements
- narrower job-specific file selection
- hardware metadata logging
- Slurm output upload
- Amber multi-step progress under one parent ELN entry

## Files Changed for Version 3

Core:

- `bmdsubmit.py`
- `api/elabftw_client.py`
- `scripts/bmdeln_epilog.py`
- `parsers/__init__.py`

Parsers:

- `parsers/cp2k_parser.py`
- `parsers/molpro_parser.py`

New file:

- `parsers/workflow_detector.py`

## Design Direction

Version 3 formalizes a two-layer parsing model.

### Layer 1: workflow-aware detection
This understands the Slurm script structure.

### Layer 2: code-specific parsing
This parses the actual input/output files for CP2K, Molpro, ORCA, Amber, OpenMolcas, etc.

This is more robust than forcing every workflow into a one-input / one-output pattern.

## Scope and Limits

Version 3 aims for:

- **one ELN entry per overall job/workflow**
- internal summaries / progress tracking for substeps or array tasks

It does **not** aim to create one ELN entry per array task or per loop iteration by default.

## Still Out of Scope

- automatic reconciliation of jobs cancelled while still pending before start
- perfect parsing of arbitrary highly dynamic shell scripts
- one-entry-per-subtask logging for large arrays

## Recommended Validation After Install

1. `bmdsubmit --help`
2. CP2K single job
3. Molpro single job
4. Amber multi-step job
5. loop workflow submitter
6. array workflow submitter

## Deployment Note

Version 3 is still the **client-side logger** that runs on BMDCluster. The eLabFTW server remains on BMDPC2.
