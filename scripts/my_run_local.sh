cd ..
set -a; source .env; set +a
./scripts/run_local.sh
# app will be running on https://console-lab.agenticthings.com/?token=<your A2ALAB_TOKEN from .env>