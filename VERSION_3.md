## auto-eln-logger v3.0

This release adds workflow-aware logging for eLabFTW-integrated Slurm jobs on HPC systems.

### New in v3.0

* Workflow detection from the Slurm submit script via `workflow_detector.py`
* Support for:

  * standard single jobs
  * loop workflows
  * Slurm array workflows
  * multi-step chained workflows
* Script-only submission support where a static top-level input file is not meaningful
* One ELN entry per overall workflow instead of one entry per internal step/task
* Correct native eLabFTW status-card updates:

  * Queued
  * Running
  * Success
  * Need to be redone
  * Fail
* Queue-aware status handling for pending jobs
* Improved job-specific file attachment logic
* Improved CP2K result extraction
* Fixed Molpro termination detection
* Added parser support for:

  * CP2K
  * Molpro
  * ORCA
  * Amber
  * OpenMolcas

### Notes

* The logger is intended to run on the cluster where jobs are submitted.
* eLabFTW remains server-side and is accessed through user-specific API tokens.
* Pending jobs cancelled before start are not yet fully reconciled automatically.

### Typical usage

* Clone the repository
* Run `bash setup.sh`
* Configure `~/.bmdeln/config.env`
* Submit jobs with `bmdsubmit`

