const mqtt = require('mqtt');
const db = require('./database');

let client = null;

const TOPICS = {
  TELEMETRY: 'iot/+/telemetry',
  STATUS: 'iot/+/status',
  ACCESS: 'iot/+/access',
  ALERT: 'iot/+/alert',
  COMMAND: 'iot/+/command'
};

async function connect() {
  const options = {
    username: process.env.MQTT_USERNAME,
    password: process.env.MQTT_PASSWORD,
    clientId: 'iot-api-server',
    clean: true,
    reconnectPeriod: 5000
  };

  client = mqtt.connect(`mqtt://${process.env.MQTT_HOST}:${process.env.MQTT_PORT}`, options);

  client.on('connect', () => {
    console.log('MQTT connected');
    client.subscribe(Object.values(TOPICS), (err) => {
      if (err) console.error('MQTT subscribe error:', err);
    });
  });

  client.on('message', handleMessage);
  client.on('error', (err) => console.error('MQTT error:', err));
}

async function handleMessage(topic, message) {
  try {
    const data = JSON.parse(message.toString());
    const parts = topic.split('/');
    const gatewayId = parts[1];
    const type = parts[2];

    switch (type) {
      case 'telemetry':
        await saveTelemetry(gatewayId, data);
        break;
      case 'status':
        await saveStatus(gatewayId, data);
        break;
      case 'access':
        await saveAccess(gatewayId, data);
        break;
      case 'alert':
        await saveAlert(gatewayId, data);
        break;
    }
  } catch (error) {
    console.error('MQTT message handler error:', error);
  }
}

async function saveTelemetry(gatewayId, data) {
  await db.query(
    `INSERT INTO telemetry (time, device_id, gateway_id, temperature, humidity, data)
     VALUES (NOW(), $1, $2, $3, $4, $5)`,
    [data.device_id, gatewayId, data.temperature, data.humidity, JSON.stringify(data)]
  );
}

async function saveStatus(gatewayId, data) {
  await db.query(
    `INSERT INTO device_status (time, device_id, gateway_id, status, sequence, metadata)
     VALUES (NOW(), $1, $2, $3, $4, $5)`,
    [data.device_id, gatewayId, data.status, data.sequence, JSON.stringify(data)]
  );
  
  await db.query(
    `UPDATE devices SET status = $1, last_seen = NOW() WHERE device_id = $2`,
    [data.status, data.device_id]
  );
}

async function saveAccess(gatewayId, data) {
  await db.query(
    `INSERT INTO access_logs (time, device_id, gateway_id, method, result, password_id, rfid_uid, deny_reason, metadata)
     VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8)`,
    [data.device_id, gatewayId, data.method, data.result, data.password_id, data.rfid_uid, data.deny_reason, JSON.stringify(data)]
  );
}

async function saveAlert(gatewayId, data) {
  await db.query(
    `INSERT INTO alerts (time, device_id, gateway_id, alert_type, severity, value, threshold, message, metadata)
     VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8)`,
    [data.device_id, gatewayId, data.alert_type, data.severity, data.value, data.threshold, data.message, JSON.stringify(data)]
  );
}

function publish(topic, message) {
  if (client && client.connected) {
    client.publish(topic, JSON.stringify(message));
  }
}

module.exports = {
  connect,
  publish,
  TOPICS
};