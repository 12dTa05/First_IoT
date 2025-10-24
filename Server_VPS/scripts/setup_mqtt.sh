#!/bin/bash
set -e

if [ -z "$MQTT_USER" ] || [ -z "$MQTT_PASSWORD" ]; then
    echo "MQTT_USER and MQTT_PASSWORD must be set"
    exit 1
fi

docker-compose exec mosquitto mosquitto_passwd -c -b /mosquitto/config/passwd "Gateway1" "2003"
echo "MQTT user created successfully"