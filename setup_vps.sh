#!/usr/bin/env bash
#
# setup_vps.sh
# ============
# One-shot installer for the Cinevo trailer pipeline on a fresh Ubuntu/Debian
# VPS. Run it ONCE as root (or with sudo):
#
#     sudo bash setup_vps.sh
#
# WHAT IT DOES
# ------------
#   1. Installs system packages: python3, pip, venv, ffmpeg, fonts, git, curl
#   2. Creates a dedicated unprivileged user (`cinevo`) to run the pipeline
#   3. Creates /opt/cinevo, clones/copies the project there
#   4. Creates a Python virtualenv and installs requirements + yt-dlp + edge-tts
#   5. Installs the systemd service + daily timer
#   6. Prints the exact next steps (secrets, first run)
#
# It is IDEMPOTENT: re-running it upgrades packages and refreshes the units
# without destroying your .env, database, or cached trailers.
#
# SUPPORTED: Ubuntu 20.04+ / Debian 11+ (x86_64 or arm64).

set -euo pipefail

# --------------------------------------------------------------------------- #
# Configuration (override with environment variables if you like)
# --------------------------------------------------------------------------- #
APP_USER="${APP_USER:-cinevo}"
APP_DIR="${APP_DIR:-/opt/cinevo}"
REPO_URL="${REPO_URL:-https://github.com/Dalia3z/cinevo-social-bot.git}"
BRANCH="${BRANCH:-master}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_NAME="cinevo-trailer"
TIMER_NAME="cinevo-trailer"

# Colours (disabled when not a TTY).
if [ -t 1 ]; then
  C_RESET="\033[0m"; C_INFO="\033[1;34m"; C_OK="\033[1;32m"; C_WARN="\033[1;33m"; C_ERR="\033[1;31m"
else
  C_RESET=""; C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""
fi

info()  { printf "${C_INFO}[INFO]${C_RESET}  %s\n" "$*"; }
ok()    { printf "${C_OK}[ OK ]${C_RESET}  %s\n" "$*"; }
warn()  { printf "${C_WARN}[WARN]${C_RESET}  %s\n" "$*"; }
err()   { printf "${C_ERR}[FAIL]${C_RESET}  %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

# --------------------------------------------------------------------------- #
# 0. Pre-flight
# --------------------------------------------------------------------------- #
if [ "$(id -u)" -ne 0 ]; then
  die "Please run as root:  sudo bash setup_vps.sh"
fi

if [ ! -f /etc/debian_version ]; then
  die "This installer targets Debian/Ubuntu. Detected a non-Debian system."
fi

info "Installing the Cinevo trailer pipeline into ${APP_DIR} as user '${APP_USER}'."

# --------------------------------------------------------------------------- #
# 1. System packages
# --------------------------------------------------------------------------- #
info "Updating apt and installing system packages (this can take a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  ffmpeg \
  fonts-dejavu-core fonts-liberation \
  git curl ca-certificates \
  build-essential pkg-config \
  sqlite3 \
  >/dev/null

ok "System packages installed."

# Verify FFmpeg really landed (the whole pipeline depends on it).
if ! command -v ffmpeg >/dev/null 2>&1; then
  die "ffmpeg was not installed correctly."
fi
ok "ffmpeg: $(ffmpeg -version | head -n1)"

# --------------------------------------------------------------------------- #
# 2. Service user
# --------------------------------------------------------------------------- #
if id -u "${APP_USER}" >/dev/null 2>&1; then
  info "User '${APP_USER}' already exists."
else
  info "Creating system user '${APP_USER}'..."
  useradd --system --create-home --shell /bin/bash "${APP_USER}"
  ok "User '${APP_USER}' created."
fi

# --------------------------------------------------------------------------- #
# 3. Application directory
# --------------------------------------------------------------------------- #
mkdir -p "${APP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

if [ -d "${APP_DIR}/.git" ]; then
  info "Existing checkout found; pulling latest ${BRANCH}..."
  sudo -u "${APP_USER}" git -C "${APP_DIR}" fetch --all --quiet || true
  sudo -u "${APP_USER}" git -C "${APP_DIR}" reset --hard "origin/${BRANCH}" --quiet || \
    warn "Could not fast-forward; leaving the working tree as-is."
  ok "Repository updated."
elif [ -f "${APP_DIR}/vps_runner.py" ]; then
  info "Project files already present in ${APP_DIR}; skipping clone."
else
  info "Cloning ${REPO_URL} (branch ${BRANCH})..."
  sudo -u "${APP_USER}" git clone --branch "${BRANCH}" --depth 1 \
    "${REPO_URL}" "${APP_DIR}" >/dev/null 2>&1 || \
    die "Clone failed. If the repo is private, clone it manually into ${APP_DIR}."
  ok "Repository cloned."
fi

# --------------------------------------------------------------------------- #
# 4. Python environment
# --------------------------------------------------------------------------- #
info "Creating the virtualenv..."
sudo -u "${APP_USER}" "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
VENV_PIP="${APP_DIR}/.venv/bin/pip"
VENV_PY="${APP_DIR}/.venv/bin/python"

info "Upgrading pip..."
sudo -u "${APP_USER}" "${VENV_PIP}" install --quiet --upgrade pip wheel setuptools

if [ -f "${APP_DIR}/requirements.txt" ]; then
  info "Installing requirements.txt..."
  sudo -u "${APP_USER}" "${VENV_PIP}" install --quiet -r "${APP_DIR}/requirements.txt"
  ok "Project requirements installed."
else
  warn "requirements.txt not found; skipping."
fi

info "Installing yt-dlp (trailer downloader)..."
sudo -u "${APP_USER}" "${VENV_PIP}" install --quiet --upgrade yt-dlp
ok "yt-dlp: $(sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/yt-dlp" --version 2>/dev/null || echo 'installed')"

info "Installing edge-tts (AI voiceover)..."
sudo -u "${APP_USER}" "${VENV_PIP}" install --quiet --upgrade edge-tts || \
  warn "edge-tts failed to install; voiceovers will be skipped."
ok "Python environment ready."

# --------------------------------------------------------------------------- #
# 5. Runtime directories
# --------------------------------------------------------------------------- #
info "Creating runtime directories..."
sudo -u "${APP_USER}" mkdir -p \
  "${APP_DIR}/trailer_clips" \
  "${APP_DIR}/trailer_output" \
  "${APP_DIR}/content_videos" \
  "${APP_DIR}/logs"
ok "Directories ready."

# --------------------------------------------------------------------------- #
# 6. .env bootstrap
# --------------------------------------------------------------------------- #
if [ ! -f "${APP_DIR}/.env" ]; then
  info "Creating a .env template (you MUST fill this in)..."
  cat > "${APP_DIR}/.env" <<'ENVEOF'
# ---------------------------------------------------------------------------
# Cinevo trailer pipeline - VPS configuration
# Fill in every value marked CHANGE_ME, then run:
#     sudo systemctl start cinevo-trailer.service
# ---------------------------------------------------------------------------

# --- DeepSeek (script generation) -----------------------------------------
DEEPSEEK_API_KEY=CHANGE_ME
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# --- Brand -----------------------------------------------------------------
WEBSITE_URL=https://cinevoapp.com
BRAND_NAME=Cinevo

# --- YouTube OAuth (upload) ------------------------------------------------
YOUTUBE_OAUTH_CLIENT_ID=CHANGE_ME
YOUTUBE_OAUTH_CLIENT_SECRET=CHANGE_ME
YOUTUBE_OAUTH_REFRESH_TOKEN=CHANGE_ME
YOUTUBE_API_KEY=CHANGE_ME

# --- Content subsystem gates ----------------------------------------------
CONTENT_ENABLED=true
CONTENT_AUTO_PUBLISH=false
CONTENT_DAILY_PUBLISH_LIMIT=1
CONTENT_PRIVACY=unlisted
CONTENT_LANGUAGE=en
CONTENT_POSTS_PER_DAY=1
CONTENT_VOICE=en-US-AriaNeural
CONTENT_ENABLE_VOICE=true
CONTENT_VIDEO_DIR=content_videos

# --- Trailer pipeline ------------------------------------------------------
TRAILER_ENABLED=true
TRAILER_CLIPS_DIR=trailer_clips
TRAILER_OUTPUT_DIR=trailer_output
TRAILER_DOWNLOAD_TIMEOUT=600
TRAILER_CLIP_SECONDS=3.5
TRAILER_CLIPS_PER_VIDEO=5
TRAILER_DOWNLOADS_PER_RUN=2
TRAILER_KEN_BURNS=true
TRAILER_COLOR_GRADE=true
TRAILER_MIRROR=false
TRAILER_SPEED_RAMP=false
TRAILER_ATTRIBUTION=true

# Comma separated titles to build videos around (optional).
# TRAILER_TITLES=Sinister,True Detective,Se7en
# Comma separated trailer URLs to use instead of searching (optional).
# TRAILER_SOURCE_URLS=https://www.youtube.com/watch?v=XXXX

# --- Logging ---------------------------------------------------------------
LOG_LEVEL=INFO
ENVEOF
  chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
  chmod 600 "${APP_DIR}/.env"
  warn "Created ${APP_DIR}/.env - EDIT IT before the first run."
else
  info ".env already exists; leaving it untouched."
fi

# --------------------------------------------------------------------------- #
# 7. systemd service + timer
# --------------------------------------------------------------------------- #
info "Installing systemd units..."

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<UNITEOF
[Unit]
Description=Cinevo trailer pipeline (download -> clip -> compose -> publish)
Documentation=https://github.com/Dalia3z/cinevo-social-bot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/vps_runner.py --count 1
# A full download + render can take a while; give it plenty of room.
TimeoutStartSec=3600
# Never let a crash loop hammer YouTube.
Restart=no
StandardOutput=append:${APP_DIR}/logs/trailer.log
StandardError=append:${APP_DIR}/logs/trailer.log

# --- Hardening -------------------------------------------------------------
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=${APP_DIR}
UNITEOF

cat > "/etc/systemd/system/${TIMER_NAME}.timer" <<TIMEREOF
[Unit]
Description=Run the Cinevo trailer pipeline daily

[Timer]
# 15:00 UTC every day. Adjust to your audience's peak time.
OnCalendar=*-*-* 15:00:00
# If the VPS was off at 15:00, run shortly after boot instead of skipping.
Persistent=true
# Spread the load a little so we do not hit YouTube at exactly :00.
RandomizedDelaySec=900
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
TIMEREOF

systemctl daemon-reload
systemctl enable "${TIMER_NAME}.timer" >/dev/null 2>&1
ok "systemd service + timer installed and enabled."

# --------------------------------------------------------------------------- #
# 8. Summary
# --------------------------------------------------------------------------- #
cat <<SUMMARYEOF

${C_OK}============================================================${C_RESET}
${C_OK} Cinevo trailer pipeline installed successfully${C_RESET}
${C_OK}============================================================${C_RESET}

  App directory : ${APP_DIR}
  Service user  : ${APP_USER}
  Python        : ${APP_DIR}/.venv/bin/python
  Logs          : ${APP_DIR}/logs/trailer.log

${C_WARN}NEXT STEPS${C_RESET}

  1. Edit the secrets file:
       sudo nano ${APP_DIR}/.env

     Fill in DEEPSEEK_API_KEY and the YOUTUBE_OAUTH_* values.
     Leave CONTENT_AUTO_PUBLISH=false for now.

  2. Do a safe preview run (renders a video, uploads nothing):
       sudo -u ${APP_USER} ${APP_DIR}/.venv/bin/python \\
         ${APP_DIR}/vps_runner.py --no-publish --title "Sinister"

     The MP4 lands in ${APP_DIR}/content_videos/.

  3. Review the video. When you are happy, enable publishing:
       sudo sed -i 's/^CONTENT_AUTO_PUBLISH=.*/CONTENT_AUTO_PUBLISH=true/' \\
         ${APP_DIR}/.env

  4. Start the daily timer:
       sudo systemctl start ${TIMER_NAME}.timer
       systemctl list-timers | grep cinevo

  5. Watch the logs:
       tail -f ${APP_DIR}/logs/trailer.log

${C_WARN}REMINDER${C_RESET}
  Re-using trailer footage can trigger a YouTube Content ID claim.
  Keep CONTENT_PRIVACY=unlisted until you have reviewed several uploads.

SUMMARYEOF

ok "Done."
