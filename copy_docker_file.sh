#!/bin/bash
# copy_docker_file.sh
# Usage: ./copy_docker_file.sh <REMOTE_USER> <REMOTE_IP> <CONTAINER_NAME> <FILE_IN_CONTAINER> <LOCAL_DESTINATION>

# --- Input parameters ---
REMOTE_USER="$1"         # e.g., root
REMOTE_IP="$2"           # e.g., 192.168.1.100
CONTAINER_NAME="$3"      # e.g., django_web
FILE_IN_CONTAINER="$4"   # e.g., /app/db/db.sqlite3
LOCAL_DEST="$5"          # e.g., ~/Downloads/

# --- Check input ---
if [ $# -ne 5 ]; then
    echo "Usage: $0 <REMOTE_USER> <REMOTE_IP> <CONTAINER_NAME> <FILE_IN_CONTAINER> <LOCAL_DESTINATION>"
    exit 1
fi

# --- Extract filename ---
FILE_NAME=$(basename "$FILE_IN_CONTAINER")

# --- Copy file from Docker container to remote host ---
echo "Copying $FILE_IN_CONTAINER from container $CONTAINER_NAME on $REMOTE_IP..."
ssh -p 2222 "$REMOTE_USER@$REMOTE_IP" "docker cp $CONTAINER_NAME:$FILE_IN_CONTAINER /tmp/$FILE_NAME"

# --- Copy file from remote host to local machine ---
echo "Copying /tmp/$FILE_NAME from $REMOTE_IP to $LOCAL_DEST..."
scp -P 2222 "$REMOTE_USER@$REMOTE_IP:/tmp/$FILE_NAME" "$LOCAL_DEST"

# --- Clean up remote temporary file ---
ssh -p 2222 "$REMOTE_USER@$REMOTE_IP" "rm -f /tmp/$FILE_NAME"

echo "Done! File copied to $LOCAL_DEST/$FILE_NAME"
