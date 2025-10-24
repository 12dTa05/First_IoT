#!/bin/bash

echo "=== IoT VPS Server Setup ==="

# Check if .env exists
if [ ! -f .env ]; then
    echo "Creating .env file..."
    cp .env.example .env
    
    # Generate random passwords
    DB_PASS=$(openssl rand -base64 32)
    MQTT_PASS=$(openssl rand -base64 32)
    JWT_SECRET=$(openssl rand -base64 64)
    
    sed -i "s/your_strong_db_password_here/$DB_PASS/g" .env
    sed -i "s/your_mqtt_password_here/$MQTT_PASS/g" .env
    sed -i "s/your_jwt_secret_key_here/$JWT_SECRET/g" .env
    
    echo "✓ .env file created with random passwords"
else
    echo "✓ .env file already exists"
fi

# Setup MQTT password
echo ""
echo "Setting up MQTT password..."
source .env
docker run -it --rm -v $(pwd)/mosquitto/passwd:/mosquitto/passwd eclipse-mosquitto:2 \
    sh -c "echo '$MQTT_PASSWORD' | mosquitto_passwd -c /mosquitto/passwd/passwd $MQTT_USERNAME"

echo "✓ MQTT password configured"

# Start services
echo ""
echo "Starting services..."
docker-compose up -d

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Services:"
echo "  - API: http://localhost:80/api"
echo "  - MQTT: localhost:1883"
echo "  - PostgreSQL: localhost:5432"
echo ""
echo "Default login:"
echo "  - Username: admin"
echo "  - Password: admin123"
echo ""
echo "MQTT Credentials:"
echo "  - Username: $MQTT_USERNAME"
echo "  - Password: (check .env file)"
echo ""
echo "Check status: docker-compose ps"
echo "View logs: docker-compose logs -f api"