const mqtt = require('mqtt');
const db = require('./database');
// const { verifyGatewayOwnership, verifyDeviceOwnership } = require('../utils/ownership');

let client = null;

function connect() {
  return new Promise((resolve, reject) => {
    const host = process.env.MQTT_HOST || 'mosquitto';
    const port = process.env.MQTT_PORT || 1883;

    const url = `mqtt://${host}:${port}`;
    
    // Kết nối MQTT không bảo mật - không username, password, không TLS
    const options = {
      clean: false,
      clientId: 'iot-api-server',
      reconnectPeriod: 5000,
      connectTimeout: 30000
    };
    
    client = mqtt.connect(url, options);

    client.on('connect', () => {
      console.log('MQTT connected to', url);
      client.subscribe('gateway/#', (err) => {
        if (err) {
          console.error('Subscribe error:', err);
        } else {
          console.log('Subscribed to gateway/#');
        }
      });
      resolve();
    });

    client.on('message', (topic, message) => {
      console.log(`📨 MQTT Received → ${topic}: ${message.toString()}`);
      handleMessage(topic, message);
    });

    client.on('error', (err) => {
      console.error('MQTT Error:', err);
      reject(err);
    });

    client.on('close', () => {
      console.log('MQTT connection closed');
    });

    client.on('reconnect', () => {
      console.log('MQTT reconnecting...');
    });
  });
}

async function handleMessage(topic, message) {
  console.log(`📨 MQTT Received → ${topic}: ${message.toString()}`);

  try {
    const data = JSON.parse(message.toString());
    const parts = topic.split('/');

    // gateway/{gatewayId}/{type}/{deviceId?}
    const gatewayId = parts[1];
    const messageType = parts[2];
    let deviceId = parts[3] || null;

    // Lấy user từ bảng gateways
    const userQuery = await db.query(
      `SELECT user_id FROM gateways WHERE gateway_id = $1 LIMIT 1`,
      [gatewayId]
    );
    if (userQuery.rowCount === 0) return;
    const userId = userQuery.rows[0].user_id;

    // → Nếu là heartbeat của gateway:
    //    gateway/GatewayX/status/gateway
    if (messageType === 'status' && deviceId === 'gateway') {
      deviceId = null; // không yêu cầu thiết bị
    }

    // Nếu có deviceId thì kiểm tra quyền sở hữu thiết bị
    // if (deviceId) {
    //   const isOwner = await verifyDeviceOwnership(deviceId, userId);
    //   if (!isOwner) return; // không lưu nếu không phải thiết bị của user này
    // }

    switch (messageType) {
      case 'telemetry':
        await saveTelemetry(gatewayId, userId, deviceId, data);
        break;

      case 'status':
        await saveDeviceStatus(gatewayId, userId, deviceId, data);
        break;

      case 'access':
        await saveAccessLog(gatewayId, userId, deviceId, data);
        break;

      case 'alert':
        await saveAlert(gatewayId, userId, deviceId, data);
        break;

      default:
        // Không làm gì
        return;
    }

  } catch (err) {
    console.error('Error handling MQTT message:', err);
  }
}

async function saveTelemetry(gatewayId, userId, deviceId, data) {
  await db.query(
    `INSERT INTO telemetry (time, device_id, gateway_id, user_id, data)
     VALUES (NOW(), $1, $2, $3, $4)`,
    [deviceId, gatewayId, userId, JSON.stringify(data)]
  );
}

async function saveDeviceStatus(gatewayId, userId, deviceId, data) {
  await db.query(
    `INSERT INTO device_status (time, device_id, gateway_id, user_id, status, sequence, metadata)
     VALUES (NOW(), $1, $2, $3, $4, $5, $6)`,
    [
      deviceId, // lúc này có thể là NULL (gateway heartbeat)
      gatewayId,
      userId,
      data.status || 'UNKNOWN',
      data.sequence || null,
      JSON.stringify(data)
    ]
  );
}

async function saveAccessLog(gatewayId, userId, deviceId, data) {
  await db.query(
    `INSERT INTO access_logs (time, device_id, gateway_id, user_id, action, granted, metadata)
     VALUES (NOW(), $1, $2, $3, $4, $5, $6)`,
    [
      deviceId,
      gatewayId,
      userId,
      data.action || '',
      data.granted || false,
      JSON.stringify(data)
    ]
  );
}

async function saveAlert(gatewayId, userId, deviceId, data) {
  await db.query(
    `INSERT INTO alerts (time, device_id, gateway_id, user_id, level, message, metadata)
     VALUES (NOW(), $1, $2, $3, $4, $5, $6)`,
    [
      deviceId,
      gatewayId,
      userId,
      data.level || 'info',
      data.message || '',
      JSON.stringify(data)
    ]
  );
}

function publish(topic, message) {
  if (!client || !client.connected) {
    console.error('MQTT client not connected');
    return false;
  }
  
  const payload = typeof message === 'string' ? message : JSON.stringify(message);
  client.publish(topic, payload, { qos: 0 }, (err) => {
    if (err) {
      console.error('Publish error:', err);
    } else {
      console.log(`📤 MQTT Published → ${topic}: ${payload}`);
    }
  });
  return true;
}

module.exports = { connect, publish };