#!/bin/sh
set -e

# migrate runs in web-entrypoint.sh only; compose waits for web to be healthy
exec celery -A payment_system worker -B -l info --pool=solo
