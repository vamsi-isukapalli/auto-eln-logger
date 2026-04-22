#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.bmdeln"
CONFIG_FILE="$CONFIG_DIR/config.env"
REAL_SBATCH_PATH="$(command -v sbatch || true)"

printf '=== BMD ELN Logger Setup ===\n'
printf 'Install dir: %s\n' "$INSTALL_DIR"
printf 'Bin dir:     %s\n\n' "$BIN_DIR"

python3 --version >/dev/null 2>&1 || { echo 'ERROR: python3 not found'; exit 1; }
printf '✅ Python3 found: %s\n' "$(python3 --version 2>&1)"

printf '\nInstalling Python dependency requests...\n'
pip3 install --user requests >/dev/null
printf '✅ Dependencies installed\n'

mkdir -p "$CONFIG_DIR/jobs" "$BIN_DIR"
printf '✅ State directory created: %s\n' "$CONFIG_DIR"

cat > "$BIN_DIR/bmdsubmit" <<EOF
#!/usr/bin/env bash
exec python3 "$INSTALL_DIR/bmdsubmit.py" "\$@"
EOF
chmod +x "$BIN_DIR/bmdsubmit"
printf '✅ bmdsubmit installed at %s/bmdsubmit\n' "$BIN_DIR"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
  fi
  printf '✅ Added ~/.local/bin to ~/.bashrc\n'
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  cat > "$CONFIG_FILE" <<EOF
# BMD ELN Logger configuration
export ELABFTW_URL="https://your-elab-host:3148"
export ELABFTW_TOKEN=""
export ELABFTW_VERIFY_SSL="true"
export ELABFTW_TIMEOUT="30"
export ELABFTW_STATUS_RUNNING="1"
export ELABFTW_STATUS_SUCCESS="2"
export ELABFTW_STATUS_REDO="3"
export ELABFTW_STATUS_FAIL="4"
export BMDELN_DEFAULT_PROJECT=""
export REAL_SBATCH="${REAL_SBATCH_PATH:-/usr/bin/sbatch}"
EOF
  printf '✅ Config template created: %s\n' "$CONFIG_FILE"
else
  printf 'ℹ️  Existing config preserved: %s\n' "$CONFIG_FILE"
fi

if ! grep -Fq 'source ~/.bmdeln/config.env' "$HOME/.bashrc" 2>/dev/null; then
  echo 'source ~/.bmdeln/config.env' >> "$HOME/.bashrc"
  printf '✅ Added config loader to ~/.bashrc\n'
fi

printf '\nCurrent sbatch path: %s\n' "${REAL_SBATCH_PATH:-not found}"
printf '\nNext steps:\n'
printf '  1. Edit %s\n' "$CONFIG_FILE"
printf '  2. Set ELABFTW_URL and ELABFTW_TOKEN\n'
printf '  3. Run: source ~/.bashrc\n'
printf '  4. Test: python3 %s/api/elabftw_client.py\n' "$INSTALL_DIR"
printf '  5. Optional project tag: bmdsubmit --project <project> <input> <script>\n'
printf '  6. Submit jobs with: bmdsubmit <input> <script>\n'
