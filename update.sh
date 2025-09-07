#!/bin/bash
# A script to automatically update and restart the Quinn Bot service from Git. 

# --- Configuration ---
BOT_DIR="/home/kidcorvid/quinnbot"
SERVICE_NAME="quinnbot"
LOG_FILE="/home/kidcorvid/quinnbot/log/bot-updates.log"
VENV_PATH="venv/bin/activate" # Path to virtual environment activation script

# --- Script Logic ---

# IMPROVEMENT: Exit immediately if a command exits with a non-zero status.
set -e

# Function for logging with a timestamp
log() {
    echo "[$(date)] $1" >> $LOG_FILE
}

log "Starting update process"

cd $BOT_DIR || { log "ERROR: Could not cd into $BOT_DIR. Aborting."; exit 1; }

# Fetch the latest changes from the remote repository
log "Fetching latest changes from origin/main"
git fetch origin main

# Get the commit hashes for the local and remote versions
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    log "No new updates available. Local is already up-to-date."
    exit 0
fi

log "Updates found. Local: $LOCAL, Remote: $REMOTE"

# IMPROVEMENT: Check for dependency changes across all new commits before pulling.
# This avoids a race condition and is more accurate than checking after the pull.
NEEDS_DEP_UPDATE=false
if git diff --name-only $LOCAL $REMOTE | grep -q "requirements.txt"; then
    NEEDS_DEP_UPDATE=true
    log "Detected changes in requirements.txt."
fi

# Pull the changes from the remote repository
log "Pulling changes from origin main..."
git pull origin main >> $LOG_FILE 2>&1

# Install/update dependencies if requirements.txt was changed
if [ "$NEEDS_DEP_UPDATE" = true ]; then
    log "Updating Python dependencies..."
    source "$VENV_PATH"
    pip install -r requirements.txt >> $LOG_FILE 2>&1
    log "Dependency update complete."
fi

# Restart the bot's service
log "Restarting the $SERVICE_NAME service..."
sudo systemctl restart $SERVICE_NAME
log "Service restarted successfully."

log "Update process complete."