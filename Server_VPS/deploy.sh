#!/bin/bash
set -e

echo "=== IoT Server Deployment ==="

# Check .env exists
if [ ! -f .env ]; then
    echo "Error: .env file not found"
    echo "Copy .env.example to .env and configure it first"
    exit 1
fi

# Load environment
export $(cat .env | grep -v '^#' | xargs)

echo "1. Generating SSL certificates..."
chmod +x scripts/generate-certs.sh
./scripts/generate-certs.sh

echo "2. Building containers..."
docker-compose build

echo "3. Starting services..."
docker-compose up -d

echo "4. Waiting for database..."
sleep 10

echo "5. Setting up MQTT user..."
docker-compose exec -T mosquitto mosquitto_passwd -c -b /mosquitto/config/passwd "$MQTT_USER" "$MQTT_PASSWORD"

echo "6. Restarting MQTT..."
docker-compose restart mosquitto

echo "7. Checking services..."
docker-compose ps

echo ""
echo "=== Deployment Complete ==="
echo "API: http://localhost"
echo "MQTT: localhost:1883 (non-TLS)"
echo "MQTTS: localhost:8883 (TLS)"
echo "WebSocket MQTT: localhost:9001"
echo ""
echo "SSL CA Certificate: ./mqtt/certs/ca.crt (copy to ESP8266)"
echo ""
echo "Login credentials:"
echo "Username: \$ADMIN_USERNAME"
echo "Password: \$ADMIN_PASSWORD"
echo ""
echo "Logs: docker-compose logs -f"