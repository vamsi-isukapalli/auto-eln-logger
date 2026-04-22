# BMDELN Setup Guide

This guide documents the currently working **Version 3** installation model.

## 1. Deployment Layout

### On BMDPC2

BMDPC2 hosts the eLabFTW server:

- Docker / Docker Compose
- MySQL
- eLabFTW app container
- server-side secrets and DB passwords

Cluster users do **not** need any of that.

### On BMDCluster

Each user installs the BMDELN client in their own account.

Example path:

```bash
~/bmdeln-v2/elnv3_work
```

The path may differ, but `setup.sh` must be run from the final install location because the launcher records that path.

## 2. Copy the Code

Users can copy the working BMDELN folder into their home directory, for example:

```bash
cp -r /path/to/shared/elnv3_work ~/bmdeln
cd ~/bmdeln
```

Do not run directly from a world-writable shared folder unless you trust all writers.

## 3. Install the Launcher

```bash
bash setup.sh
source ~/.bashrc
```

This creates:

- `~/.local/bin/bmdsubmit`
- `~/.bmdeln/`
- `~/.bmdeln/jobs/`
- log files under `~/.bmdeln/`

## 4. User Configuration

Edit:

```bash
~/.bmdeln/config.env
```

Recommended contents:

```bash
export ELABFTW_URL="https://HOST:3148" #get this from ADMIN
export ELABFTW_TOKEN="YOUR_OWN_API_TOKEN"
export ELABFTW_VERIFY_SSL="false"
export ELABFTW_TIMEOUT="30"
export REAL_SBATCH="/usr/bin/sbatch"

export ELABFTW_STATUS_RUNNING="1"
export ELABFTW_STATUS_SUCCESS="2"
export ELABFTW_STATUS_REDO="3"
export ELABFTW_STATUS_FAIL="4"
export ELABFTW_STATUS_QUEUED="5"

export BMDELN_DEFAULT_PROJECT=""
```

Then load it:

```bash
source ~/.bmdeln/config.env
source ~/.bashrc
```

## 5. eLabFTW API Token

Each user must generate their own token from their own eLabFTW account.

In the eLabFTW web UI:

1. log in
2. click user menu / initials
3. open **Settings**
4. go to **API Keys**
5. create a new key
6. give it a name such as `bmdsubmit-cluster`
7. set permission to **Read/Write**
8. generate and copy the token
9. paste it into `ELABFTW_TOKEN`

## 6. Required eLabFTW Statuses

These experiment statuses must exist on the server:

- `Running`
- `Success`
- `Need to be redone`
- `Fail`
- `Queued`

Current ID mapping in the tested deployment:

- `Running = 1`
- `Success = 2`
- `Need to be redone = 3`
- `Fail = 4`
- `Queued = 5`

If the IDs change on another server, update `config.env` accordingly.

## 7. Verify the Install

Check the launcher:

```bash
which bmdsubmit
head -20 ~/.local/bin/bmdsubmit
```

Check API connectivity:

```bash
python3 /path/to/your/install/api/elabftw_client.py
```

Expected output:

```text
Connecting to: https://10.153.70.15:3148/api/v2
✅ Connection OK
```

## 8. Submission Modes

### A. Single-job mode

```bash
bmdsubmit input.inp submit.sh
```

### B. Script-only workflow mode

Use when the submitter determines the real inputs and outputs internally.

```bash
bmdsubmit submit.sh
```

### C. Add a project tag

```bash
bmdsubmit --project WATER-PROJECT input.inp submit.sh
```

or

```bash
bmdsubmit --project WATER-PROJECT submit.sh
```

## 9. What Works in Version 3

- native eLabFTW status card updates
- `Queued -> Running -> Success/Fail`
- CP2K single jobs
- Molpro single jobs
- Amber multi-step jobs under one ELN entry
- script-only workflows
- Slurm arrays under one ELN entry
- hardware metadata logging
- Slurm stdout upload
- better file selection (no broad unrelated `*.xyz` sweeps)

## 10. Known Limitations

- pending `scancel` before job start is not reconciled automatically
- highly dynamic workflow scripts may not be fully inferable
- arrays and workflow jobs are represented as one parent entry, not one entry per subtask

## 11. Troubleshooting

### `bmdsubmit: command not found`

```bash
source ~/.bashrc
which bmdsubmit
```

### API connection fails

Check:

```bash
cat ~/.bmdeln/config.env
python3 /path/to/install/api/elabftw_client.py
```

### Job stays in wrong status

Check:

- `~/.bmdeln/bmdsubmit.log`
- `~/.bmdeln/epilog.log`
- your eLabFTW status IDs in `config.env`

### Wrong files appear in the entry

This should now be much improved, but if it still happens, keep the submitter, input, and directory listing for debugging.

## 12. Upgrade Procedure

When replacing an older BMDELN install with a newer one:

1. keep the old folder as backup
2. copy the new folder into the final location
3. run `bash setup.sh` from that final location
4. `source ~/.bashrc`
5. keep the same `~/.bmdeln/config.env` unless new variables were added
