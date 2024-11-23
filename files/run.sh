#!/bin/bash
set +u
CONFIG_PATH=/data/options.json

echo "Starting EufyP2PStream"
echo "Config path is $CONFIG_PATH"
echo "Config content is $(cat $CONFIG_PATH)"

EUFY_WS_PORT=$(jq --raw-output ".eufy_security_ws_port" $CONFIG_PATH)
CAM1_SN=$(jq --raw-output ".camera_1_serial_number" $CONFIG_PATH)
CAM2_SN=$(jq --raw-output ".camera_2_serial_number" $CONFIG_PATH)
CAM3_SN=$(jq --raw-output ".camera_3_serial_number" $CONFIG_PATH)
CAM4_SN=$(jq --raw-output ".camera_4_serial_number" $CONFIG_PATH)
CAM5_SN=$(jq --raw-output ".camera_5_serial_number" $CONFIG_PATH)

echo "Starting EufyP2PStream. eufy_security_ws_port is $EUFY_WS_PORT"
python3 -u /eufyp2pstream.py --ws_security_port $EUFY_WS_PORT --serials $CAM1_SN $CAM2_SN $CAM3_SN $CAM4_SN $CAM5_SN
echo "Exited with code $?"