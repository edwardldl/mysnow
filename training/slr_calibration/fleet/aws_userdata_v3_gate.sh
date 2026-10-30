#!/bin/bash
# MySnow Phase 3 v3 -- VERIFICATION GATE instance user-data.
# Launch contract: c7i.4xlarge ON-DEMAND, us-west-2, instance profile
# mysnow-era5-extractor, --instance-initiated-shutdown-behavior
# terminate, tags project=mysnow / purpose=era5-extraction.
# Installs the v3 bundle, syncs the 20 workspace reference parts,
# re-extracts them in-region via gate_v3_pl.py (production reader +
# payload path), diffs every value (tol 1e-3), uploads the result and
# a _GATE_PASS / _GATE_FAIL marker to v3-features/, then shuts down
# (which terminates).  Hard self-destruct +120 min regardless.
set -x
exec > /var/log/mysnow-v3-gate.log 2>&1
shutdown -h +120
export DEBIAN_FRONTEND=noninteractive HOME=/root
B=mysnow-era5-results-965888290243
apt-get update -y && apt-get install -y python3-venv python3-pip unzip curl
curl -s https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install
aws s3 cp s3://$B/bundle/mysnow-v3-bundle.tar.gz /opt/bundle.tar.gz
mkdir -p /opt/mysnow && tar -xzf /opt/bundle.tar.gz -C /opt/mysnow
cd /opt/mysnow/training/slr_calibration
python3 -m venv .venv && .venv/bin/pip install -q -e ".[noaa,ml,era5,cds]"
mkdir -p /opt/gate_reference
aws s3 sync s3://$B/v3-features/gate_reference/ /opt/gate_reference --no-progress
find /opt/gate_reference -name "*.json.gz" | wc -l
PY=/opt/mysnow/training/slr_calibration/.venv/bin/python
date -u +"GATE_START %FT%TZ"
$PY /opt/mysnow/gate_v3_pl.py --root /opt/mysnow --reference /opt/gate_reference --out /opt/gate_out > /tmp/gate_result.txt 2>&1
RC=$?
date -u +"GATE_END %FT%TZ rc=$RC"
cat /tmp/gate_result.txt
aws s3 cp /tmp/gate_result.txt s3://$B/v3-features/gate_result.txt
if [ $RC -eq 0 ]; then
  touch /tmp/gp && aws s3 cp /tmp/gp s3://$B/v3-features/_GATE_PASS
else
  touch /tmp/gf && aws s3 cp /tmp/gf s3://$B/v3-features/_GATE_FAIL
fi
aws s3 cp /var/log/mysnow-v3-gate.log s3://$B/v3-features/gate_bootstrap.log
shutdown -h now   # terminates (shutdown behavior set at launch)
