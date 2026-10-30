#!/bin/bash
# Workspace-side sync for the Phase 3 v3 in-region fleet (October pattern):
# pull v3-features/pl_parts/ into the local model-v3-features tree every
# 5 minutes, print the local part count each pass, and exit after a final
# sync once all four _DONE_shard markers exist (exact-key head-object
# checks).  Exits non-zero immediately if any _FAILED marker appears.
set -u
B=mysnow-era5-results-965888290243
DEST=/home/coder/mysnow/data/slr-training-data/screened-v2/model-v1/model-v3-features/pl_parts
AWS=/home/coder/.local/bin/aws
exec 9>/tmp/sync_v3_parts.lock
flock -n 9 || { echo "another sync_v3_parts is running"; exit 1; }
while true; do
  $AWS s3 sync s3://$B/v3-features/pl_parts/ "$DEST" --no-progress
  COUNT=$(find "$DEST" -name '*.json.gz' | wc -l)
  echo "[$(date -u +%FT%TZ)] local w parts: $COUNT"
  FAILED=0; DONE=0
  for i in 0 1 2 3; do
    if $AWS s3api head-object --bucket $B --key v3-features/_FAILED_shard$i >/dev/null 2>&1; then
      echo "MARKER _FAILED_shard$i present"; FAILED=1
    fi
    if $AWS s3api head-object --bucket $B --key v3-features/_DONE_shard$i >/dev/null 2>&1; then
      DONE=$((DONE+1))
    fi
  done
  if [ $FAILED -eq 1 ]; then echo "fleet reported failure; stopping sync"; exit 2; fi
  if [ $DONE -eq 4 ]; then
    $AWS s3 sync s3://$B/v3-features/pl_parts/ "$DEST" --no-progress
    COUNT=$(find "$DEST" -name '*.json.gz' | wc -l)
    echo "[$(date -u +%FT%TZ)] ALL DONE; final local w parts: $COUNT"
    exit 0
  fi
  sleep 300
done
