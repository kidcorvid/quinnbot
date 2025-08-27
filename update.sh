#!/bin/bash
BOT_DIR="/home/kidcorvid/quinnbot"
SERVICE_NAME="quinnbot"
LOG_FILE="/home/kidcorvid/quinnbot/log/bot-updates.log"

echo "[$(date)] Starting update process" >> $LOG_FILE

cd $BOT_DIR

# Fetch latest changes
git fetch origin main

# Check if updates are available
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ $LOCAL = $REMOTE ]; then
    echo "[$(date)] No updates available" >> $LOG_FILE
    exit 0
fi

# Pull changes
git pull origin main >> $LOG_FILE 2>&1

# Install/update dependencies if requirements.txt changed
if git diff HEAD~ HEAD --name-only | grep -q "requirements.txt"; then
    echo "[$(date)] Updating dependencies" >> $LOG_FILE
    source venv/bin/activate  # if using virtual environment
    pip install -r requirements.txt >> $LOG_FILE 2>&1
fi

# Restart the service
echo "[$(date)] Restarting service" >> $LOG_FILE
sudo systemctl restart $SERVICE_NAME

echo "[$(date)] Update complete" >> $LOG_FILE