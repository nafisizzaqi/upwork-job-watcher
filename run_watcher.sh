#!/bin/bash
cd /home/user/projects/upwork-job-watcher || exit 1
mkdir -p logs
exec flock -n /tmp/upwork-job-watcher.lock timeout 480 /home/user/projects/upwork-job-watcher/.venv/bin/python main.py >> logs/watcher.log 2>&1
