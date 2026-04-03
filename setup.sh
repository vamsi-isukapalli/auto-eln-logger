#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# BMD ELN Logger — setup script
# Run once as user 'vamsi' to install for single-user prototype
# ─────────────────────────────────────────────────────────────

set -e

INSTALL_DIR="$HOME/bmdeln"
BIN_DIR="$HOME/.local/bin"

echo "=== BMD ELN Logger Setup ==="
echo "Install dir: $INSTALL_DIR"
echo "Bin dir:     $BIN_DIR"
echo ""

# ── 1. Check Python ──────────────────────────────────────────
python3 --version >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }
echo "✅ Python3 found: $(python3 --version)"

# ── 2. Install Python deps ───────────────────────────────────
echo ""
echo "Installing Python dependencies..."
pip3 install --user requests
echo "✅ Dependencies installed"

# ── 3. Create state directory ────────────────────────────────
mkdir -p "$HOME/.bmdeln/jobs"
echo "✅ State directory created: $HOME/.bmdeln/"

# ── 4. Create bin dir and symlink ────────────────────────────
mkdir -p "$BIN_DIR"

# Create the bmdsubmit launcher
cat > "$BIN_DIR/bmdsubmit" << EOF
#!/usr/bin/env bash
exec python3 "$INSTALL_DIR/bmdsubmit.py" "\$@"
EOF
chmod +x "$BIN_DIR/bmdsubmit"
echo "✅ bmdsubmit installed at $BIN_DIR/bmdsubmit"

# ── 5. Check PATH ────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "⚠️  Add this to your ~/.bashrc:"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
    # Auto-add if .bashrc exists
    if [ -f "$HOME/.bashrc" ]; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
        echo "✅ Added to ~/.bashrc (run: source ~/.bashrc)"
    fi
fi

# ── 6. Config file ───────────────────────────────────────────
CONFIG_FILE="$HOME/.bmdeln/config.env"
if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << 'EOF'
# BMD ELN Logger configuration
# Source this file or set these variables in your ~/.bashrc

# eLabFTW server URL (update once deployed)
export ELABFTW_URL="https://elab.bmdgroup.lmu.de"

# Your personal eLabFTW API token (get from eLabFTW → Profile → API Keys)
export ELABFTW_TOKEN=""

# Path to real sbatch binary (verify this is correct on your cluster)
# export REAL_SBATCH="/usr/bin/sbatch"
EOF
    echo "✅ Config template created: $CONFIG_FILE"
    echo ""
    echo "⚠️  IMPORTANT: Edit $CONFIG_FILE and set:"
    echo "    ELABFTW_URL   — your eLabFTW server URL"
    echo "    ELABFTW_TOKEN — your personal API token from eLabFTW"
    echo ""
    echo "   Then add to ~/.bashrc:"
    echo "    source $HOME/.bmdeln/config.env"
fi

# ── 7. Verify real sbatch path ────────────────────────────────
SBATCH_PATH=$(which sbatch 2>/dev/null || echo "not found")
echo "ℹ️  Current sbatch path: $SBATCH_PATH"
if [ "$SBATCH_PATH" != "not found" ]; then
    REAL_SBATCH=$(readlink -f "$SBATCH_PATH")
    echo "ℹ️  Real sbatch resolves to: $REAL_SBATCH"
    echo "   Make sure REAL_SBATCH in bmdsubmit.py matches this."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. source ~/.bashrc"
echo "  2. Edit ~/.bmdeln/config.env with your eLabFTW URL and token"
echo "  3. source ~/.bmdeln/config.env"
echo "  4. Test with: bmdsubmit H2O_64.inp submit_cp2k.sh"
echo ""
echo "Instead of:  sbatch submit_cp2k.sh"
echo "Now run:     bmdsubmit H2O_64.inp submit_cp2k.sh"
