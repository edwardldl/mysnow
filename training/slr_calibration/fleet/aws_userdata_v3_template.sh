#!/bin/bash
# MySnow Phase 3 v3 PL fetch (w) — in-region fleet user-data, instance
# __INST__ of 4.  Launch contract (October round-2 pattern): c7i.2xlarge
# ON-DEMAND, us-west-2, instance profile mysnow-era5-extractor,
# --instance-initiated-shutdown-behavior terminate, tags project=mysnow
# / purpose=era5-extraction.  This instance runs fleet shard indices
# __SHARDS__ of __SHARD_COUNT__ (strided day partition, fetch_v3_pl).
# Endgame uploads v3-features/_DONE_shard__INST__ ONLY when every shard
# process exits 0; otherwise _FAILED_shard__INST__.
set -x
exec > /var/log/mysnow-v3.log 2>&1
shutdown -h +720   # hard self-destruct in 12 h no matter what
export DEBIAN_FRONTEND=noninteractive HOME=/root
B=mysnow-era5-results-965888290243
INST=__INST__
apt-get update -y && apt-get install -y python3-venv python3-pip unzip curl
curl -s https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install
aws s3 cp s3://$B/bundle/mysnow-v3-bundle.tar.gz /opt/bundle.tar.gz
mkdir -p /opt/mysnow && tar -xzf /opt/bundle.tar.gz -C /opt/mysnow
cd /opt/mysnow/training/slr_calibration
python3 -m venv .venv && .venv/bin/pip install -q -e ".[noaa,ml,era5,cds]"
cd /opt/mysnow
PY=training/slr_calibration/.venv/bin/python
V3=data/slr-training-data/screened-v2/model-v1/model-v3-features
mkdir -p $V3/pl_parts
# Seed with every part already produced (workspace fetch + any prior
# fleet run) so the planner skips finished station-days.
aws s3 sync s3://$B/v3-features/pl_parts/ $V3/pl_parts --no-progress
date -u +"SEEDED %FT%TZ"
IFACE=$(ls /sys/class/net | grep -v lo | head -1); IFACE=${IFACE:-eth0}
echo "epoch,rx_bytes,tx_bytes" > /var/log/mysnow-net.csv
( while true; do echo "$(date -u +%s),$(cat /sys/class/net/$IFACE/statistics/rx_bytes),$(cat /sys/class/net/$IFACE/statistics/tx_bytes)" >> /var/log/mysnow-net.csv; sleep 10; done ) &
NET_PID=$!
( while true; do aws s3 sync $V3/pl_parts s3://$B/v3-features/pl_parts/ --no-progress; sleep 20; done ) &
SYNC_PID=$!
date -u +"FETCH_START %FT%TZ"
PIDS=""
for S in __SHARDS__; do
  $PY /opt/mysnow/run_v3_fleet_shard.py --shard-index $S --shard-count __SHARD_COUNT__ > /var/log/mysnow-v3-shard$S.log 2>&1 &
  PIDS="$PIDS $!"
done
RC=0
for P in $PIDS; do wait $P || RC=1; done
date -u +"FETCH_END %FT%TZ rc=$RC"
kill $SYNC_PID $NET_PID 2>/dev/null
aws s3 sync $V3/pl_parts s3://$B/v3-features/pl_parts/ --no-progress
aws s3 sync $V3/fleet_summaries s3://$B/v3-features/fleet_summaries/ --no-progress
if [ $RC -eq 0 ]; then
  touch /tmp/_DONE && aws s3 cp /tmp/_DONE s3://$B/v3-features/_DONE_shard${INST}
else
  touch /tmp/_FAILED && aws s3 cp /tmp/_FAILED s3://$B/v3-features/_FAILED_shard${INST}
fi
aws s3 cp /var/log/mysnow-net.csv s3://$B/v3-features/net_shard${INST}.csv
aws s3 cp /var/log/mysnow-v3.log s3://$B/v3-features/bootstrap_shard${INST}.log
shutdown -h now   # terminates (shutdown behavior set at launch)
