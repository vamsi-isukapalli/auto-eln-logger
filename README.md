# auto-eln-logger

**Automatic Electronic Lab Notebook logging for computational chemistry HPC clusters.**

Integrates [Slurm](https://slurm.schedmd.com/) job submission with [eLabFTW](https://www.elabftw.net/) by automatically parsing QC code input/output files and logging all calculation metadata — with zero extra effort from researchers.

Developed for the **AK Fingerhut group, LMU Munich** (theoretical and computational chemistry).

---

## The problem

Most Electronic Lab Notebook (ELN) tools are designed for experimental groups. Computational chemistry groups need to track:

- Input parameters (method, basis set, functional, geometry)
- HPC job metadata (cluster, partition, nodes, walltime)
- Results (energies, convergence, timing)
- Provenance (which calculation fed into the next)

Manually logging this is unrealistic. This tool automates it entirely.

---

## How it works

```
Researcher runs:    bmdsubmit H2O_64.inp submit_cp2k.sh
                              ↓
             Parses input file → extracts metadata
             Creates eLabFTW entry (status: Running)
             Injects epilog into job script
             Submits to Slurm → gets Job ID
             Updates eLabFTW entry with Job ID
                              ↓
                    Job runs on cluster
                              ↓
             Epilog runs at job end:
             Parses output file → extracts results
             Updates eLabFTW entry (status: Success/Fail)
             Uploads small files, logs paths of large files
```

Researchers replace `sbatch submit.sh` with `bmdsubmit input.inp submit.sh`. Everything else is automatic.

---

## Supported QC codes

| Code | Input parsing | Output parsing | Status |
|------|--------------|----------------|--------|
| CP2K | ✅ Full | ✅ Full | Production ready |
| Molpro | ✅ Full | ✅ Full | Ready |
| ORCA | 🔄 In progress | 🔄 In progress | Coming soon |
| Amber | 🔄 In progress | 🔄 In progress | Coming soon |
| OpenMolcas | 🔄 In progress | 🔄 In progress | Coming soon |

---

## What gets logged automatically

**At submission:**
- QC code, input file, project name, run type
- Method, functional, basis set, dispersion correction
- Cell parameters, geometry file, charge, multiplicity
- Slurm job ID, partition, nodes, MPI tasks, requested walltime
- Submission directory and timestamp

**At completion:**
- Job status (Success / Fail)
- Final energy (a.u.)
- SCF convergence, timing, code version
- Small files uploaded to eLabFTW
- Large files (trajectories, logs) referenced by cluster path

---

## Requirements

**On the ELN server:**
- Docker + Docker Compose v2
- Ubuntu 22.04 recommended

**On the HPC cluster:**
- Python 3.9+
- `requests` library (`pip install --user requests`)
- Slurm workload manager

---

## Installation

### 1. Deploy eLabFTW (one-time, on your group server)

```bash
mkdir ~/elabftw && cd ~/elabftw
cp /path/to/auto-eln-logger/docker/docker-compose.example.yml docker-compose.yml

# Edit docker-compose.yml — set your passwords and secret key
vi docker-compose.yml

# Generate a proper secret key
docker run --rm elabftw/elabimg:latest php bin/init tools:genkey

# Start eLabFTW
docker compose up -d
sleep 30
docker exec elabftw-app bin/init db:install
```

Open `https://localhost:3148` in your browser and register your sysadmin account.

### 2. Install bmdsubmit on the cluster (each user)

```bash
# Copy the repo to the cluster
git clone git@github.com:vamsi-isuk/auto-eln-logger.git ~/bmdeln
cd ~/bmdeln
bash setup.sh
source ~/.bashrc
```

### 3. Configure credentials

```bash
vi ~/.bmdeln/config.env
```

```bash
export ELABFTW_URL="https://YOUR_SERVER_IP:3148"
export ELABFTW_TOKEN="your-api-token-from-elabftw"
```

```bash
echo 'source ~/.bmdeln/config.env' >> ~/.bashrc
source ~/.bashrc
```

### 4. Test connectivity

```bash
python3 ~/bmdeln/api/elabftw_client.py
# Should print: ✅ Connection OK
```

---

## Usage

```bash
# Navigate to your calculation directory
cd /path/to/my/calculation

# Instead of:
sbatch submit_cp2k.sh

# Run:
bmdsubmit H2O_64.inp submit_cp2k.sh
```

Output:
```
[2026-03-30 13:29:33] Detected QC code: CP2K
[2026-03-30 13:29:33] Parsed CP2K input: project=H2O-64, run_type=MD
[2026-03-30 13:29:36] Created eLabFTW entry: ID=7
Submitted batch job 181929
[2026-03-30 13:29:36] Updated eLabFTW entry 7 with Slurm Job ID 181929
```

The entry appears immediately in your eLabFTW dashboard, tagged with code, run type, partition, and username. When the job finishes, the entry updates automatically with results.

---

## Accessing the ELN

The ELN web interface is accessible via SSH tunnel from anywhere:

```bash
# From your laptop
ssh -L 3148:YOUR_SERVER_IP:3148 username@hpc-cluster
```

Then open `https://localhost:3148` in your browser.

---

## Repository structure

```
auto-eln-logger/
├── bmdsubmit.py              # Main wrapper — replaces sbatch
├── setup.sh                  # One-time install script
├── parsers/
│   ├── cp2k_parser.py        # CP2K input + output parser
│   ├── molpro_parser.py      # Molpro input + output parser
│   └── code_detector.py      # Detects QC code from job script
├── api/
│   └── elabftw_client.py     # eLabFTW REST API client
├── scripts/
│   └── bmdeln_epilog.py      # Runs at job completion
├── docker/
│   └── docker-compose.example.yml  # eLabFTW deployment template
├── examples/
│   ├── cp2k/                 # CP2K sample input files
│   └── molpro/               # Molpro sample input files
└── SETUP_GUIDE.txt           # Detailed setup guide for group members
```

---

## Adding new group members

1. Member registers at `https://YOUR_SERVER:3148/register.php` → selects your team
2. Sysadmin activates their account
3. Member generates an API key in eLabFTW settings
4. Member installs bmdsubmit following steps 2–4 above

---

## Funding compliance

eLabFTW provides cryptographic timestamping of all entries, satisfying DFG and EU Horizon audit trail requirements. For long-term archiving and DOI minting, integration with [NOMAD](https://nomad-lab.eu) is planned.

---

## Roadmap

- [ ] ORCA parser
- [ ] Amber parser
- [ ] OpenMolcas parser
- [ ] NOMAD push script for archiving at publication
- [ ] Cancelled/timed-out job detection via cron checker
- [ ] Group member onboarding automation

---

## Citation

If you use this tool in your research, please cite it via the GitHub repository:

```
Isukapalli, S.V. (2026). auto-eln-logger: Automatic ELN logging for
computational chemistry HPC clusters. AK Fingerhut, LMU Munich.
https://github.com/vamsi-isuk/auto-eln-logger
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Contact

**Sai Vamsikrishna Isukapalli**
AK Fingerhut, Department of Chemistry, LMU Munich
vamispc@cup.uni-muenchen.de
