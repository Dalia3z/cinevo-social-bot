#!/usr/bin/env bash
#
# deploy.sh
# =========
# Deploys the Cinevo trailer pipeline from your LOCAL machine to the VPS and
# (optionally) starts the systemd timer.
#
# Run this from the project root on your own computer:
#
#     bash deploy.sh user@your-vps-ip
#
# WHAT IT DOES
# ------------
#   1. Checks that the local tree is clean and pushed to git
#   2. Copies the project to the VPS (rsync, excluding secrets/caches)
#   3. Runs setup_vps.sh on the VPS (installs FFmpeg, venv, yt-dlp, systemd)
#   4. Optionally starts the daily timer
#
# It NEVER copies your local .env, databases, or downloaded trailers: those
# live only on the VPS. Secrets are configured there, once.
#
# REQUIREMENTS (local machine)
#   * bash (Git Bash / WSL on Windows, or any Linux/macOS shell)
#   * ssh + rsync
#   * SSH key access to the VPS (password auth also works but is less safe)

set -euo pipefail

# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #
VPS_HOST="${1:-}"
REMOTE_DIR="${REMOTE_DIR:-/opt/cinevo}"
BRANCH="${BRANCH:-master}"

if [ -t 1 ]; then
  C_RESET="\033[0m"; C_INFO="\033[1;34m"; C_OK="\033[1;32m"; C_WARN="\033[1;33m"; C_ERR="\033[1;31m"
else
  C_RESET=""; C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""
fi

info() { printf "${C_INFO}[INFO]${C_RESET}  %s\n" "$*"; }
ok()   { printf "${C_OK}[ OK ]${C_RESET}  %s\n" "$*"; }
warn() { printf "${C_WARN}[WARN]${C_RESET}  %s\n" "$*"; }
die()  { printf "${C_ERR}[FAIL]${C_RESET}  %s\n" "$*" >&2; exit 1; }

if [ -z "${VPS_HOST}" ]; then
  cat <<USAGEEOF
Usage:
    bash deploy.sh user@your-vps-ip

Environment overrides:
    REMOTE_DIR=/opt/cinevo   (default)
    BRANCH=master            (default)

Example:
    bash deploy.sh root@203.0.113.42
USAGEEOF
  exit 1
fi

# --------------------------------------------------------------------------- #
# 0. Local pre-flight
# --------------------------------------------------------------------------- #
command -v ssh   >/dev/null 2>&1 || die "ssh not found. Install OpenSSH."
command -v rsync >/dev/null 2>&1 || die "rsync not found. Install rsync."

if [ ! -f "vps_runner.py" ]; then
  die "Run this from the project root (vps_runner.py not found)."
fi

if command -v git >/dev/null 2>&1 && [ -d .git ]; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    warn "You have uncommitted local changes. They WILL be copied to the VPS."
  fi
  info "Local branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
fi

info "Testing SSH connectivity to ${VPS_HOST}..."
ssh -o BatchMode=yes -o ConnectTimeout=10 "${VPS_HOST}" "echo ok" >/dev/null 2>&1 || \
  die "Cannot SSH into ${VPS_HOST}. Check the address and your SSH key."
ok "SSH connection works."

# --------------------------------------------------------------------------- #
# 1. Copy the project
# --------------------------------------------------------------------------- #
info "Copying the project to ${VPS_HOST}:${REMOTE_DIR} ..."

ssh "${VPS_HOST}" "sudo mkdir -p '${REMOTE_DIR}' && sudo chown \$(id -u):\$(id -g) '${REMOTE_DIR}'"

rsync -az --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude '*.db' \
  --exclude '*.db-journal' \
  --exclude 'logs/' \
  --exclude 'trailer_clips/' \
  --exclude 'trailer_output/' \
  --exclude 'content_videos/' \
  --exclude '.compose_work/' \
  ./ "${VPS_HOST}:${REMOTE_DIR}/"

ok "Project copied."

# --------------------------------------------------------------------------- #
# 2. Run the installer on the VPS
# --------------------------------------------------------------------------- #
info "Running setup_vps.sh on the VPS (this installs FFmpeg, venv, yt-dlp)..."
ssh -t "${VPS_HOST}" "sudo APP_DIR='${REMOTE_DIR}' bash '${REMOTE_DIR}/setup_vps.sh'"
ok "VPS setup complete."

# --------------------------------------------------------------------------- #
# 3. Optional: start the timer
# --------------------------------------------------------------------------- #
printf "\n"
read -r -p "Start the daily systemd timer now? [y/N] " REPLY || REPLY="n"
case "${REPLY}" in
  [yY]|[yY][eE][sS])
    ssh "${VPS_HOST}" "sudo systemctl start cinevo-trailer.timer && systemctl list-timers | grep cinevo || true"
    ok "Timer started."
    ;;
  *)
    info "Timer NOT started. Start it later with:"
    info "  ssh ${VPS_HOST} 'sudo systemctl start cinevo-trailer.timer'"
    ;;
esac

# --------------------------------------------------------------------------- #
# 4. Summary
# --------------------------------------------------------------------------- #
cat <<SUMMARYEOF

${C_OK}============================================================${C_RESET}
${C_OK} Deployment finished${C_RESET}
${C_OK}============================================================${C_RESET}

  VPS        : ${VPS_HOST}
  Remote dir : ${REMOTE_DIR}

${C_WARN}REMEMBER${C_RESET}
  1. Edit the secrets ON THE VPS (they are never copied from here):
       ssh ${VPS_HOST} 'sudo nano ${REMOTE_DIR}/.env'

  2. Safe preview run:
       ssh ${VPS_HOST} 'sudo -u cinevo ${REMOTE_DIR}/.venv/bin/python \\
         ${REMOTE_DIR}/vps_runner.py --no-publish --title "Sinister"'

  3. Watch the logs:
       ssh ${VPS_HOST} 'tail -f ${REMOTE_DIR}/logs/trailer.log'

SUMMARYEOF

ok "Done."
