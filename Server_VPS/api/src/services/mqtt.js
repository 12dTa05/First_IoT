const mqtt = require('mqtt');
const db = require('./database');
const fs = require('fs');
const path = require('path');

let client = null;

// Gateway ID cache để tránh query DB liên tục
const gatewayCache = new Map();

async function connect() {
  const options = {
    clientId: 'vps-api-server',
    clean: true,
    reconnectPeriod: 5000,
    // mTLS configuration
    key: fs.readFileSync(path.join(__dirname, '../../certs/server.key.pem')),
    cert: fs.readFileSync(path.join(__dirname, '../../certs/server.cert.pem')),
    ca: fs.readFileSync(path.join(__dirname, '../../certs/ca.cert.pem')),
    rejectUnauthorized: true
  };

  const host = process.env.MQTT_HOST || 'localhost';
  const port = process.env.MQTT_PORT || 8883;

  client = mqtt.connect(`mqtts://${host}:${port}`, options);

  client.on('connect', () => {
    console.log('✅ MQTT connected to broker');
    
    // Subscribe to all gateway topics
    const topics = [
      'gateway/+/telemetry/+',
      'gateway/+/status/+',
      'gateway/+/logs/+',
      'gateway/+/access/+'
    ];
    
    client.subscribe(topics, (err) => {
      if (err) {
        console.error('MQTT subscribe error:', err);
      } else {
        console.log('📡 Subscribed to gateway topics:', topics);
      }
    });
  });

  client.on('message', handleMessage);
  
  client.on('error', (err) => {
    console.error('❌ MQTT error:', err);
  });
  
  client.on('close', () => {
    console.log('🔌 MQTT connection closed');
  });
}

async function handleMessage(topic, message) {
  try {
    const data = JSON.parse(message.toString());
    const parts = topic.split('/');
    
    // Topic format: gateway/{gateway_id}/{type}/{device_id}
    const gatewayId = parts[1];
    const messageType = parts[2];
    const deviceId = parts[3];
    
    console.log(`📨 Received: ${topic}`, data);
    
    // Get user_id from gateway_id (with caching)
    const userId = await getUserIdFromGateway(gatewayId);
    
    if (!userId) {
      console.error('❌ Unknown gateway:', gatewayId);
      return;
    }
    
    // Process message based on type
    switch (messageType) {
      case 'telemetry':
        await saveTelemetry(gatewayId, userId, deviceId, data);
        break;
      case 'status':
        await saveDeviceStatus(gatewayId, userId, deviceId, data);
        break;
      case 'logs':
      case 'access':
        await saveAccessLog(gatewayId, userId, deviceId, data);
        break;
      default:
        console.warn('⚠️ Unknown message type:', messageType);
    }
  } catch (error) {
    console.error('❌ MQTT message handler error:', error);
  }
}

// Get user_id from gateway_id with caching
async function getUserIdFromGateway(gatewayId) {
  // Check cache first
  if (gatewayCache.has(gatewayId)) {
    return gatewayCache.get(gatewayId);
  }
  
  // Query database
  try {
    const result = await db.query(
      'SELECT user_id FROM gateways WHERE gateway_id = $1',
      [gatewayId]
    );
    
    if (result.rows.length === 0) {
      return null;
    }
    
    const userId = result.rows[0].user_id;
    
    // Cache for 5 minutes
    gatewayCache.set(gatewayId, userId);
    setTimeout(() => gatewayCache.delete(gatewayId), 5 * 60 * 1000);
    
    return userId;
  } catch (error) {
    console.error('Error getting user_id for gateway:', error);
    return null;
  }
}

// Verify device ownership
async function verifyDeviceOwnership(deviceId, userId) {
  const result = await db.query(
    'SELECT 1 FROM devices WHERE device_id = $1 AND user_id = $2',
    [deviceId, userId]
  );
  return result.rows.length > 0;
}

async function saveTelemetry(gatewayId, userId, deviceId, data) {
  try {
    // Verify ownership
    const isOwner = await verifyDeviceOwnership(deviceId, userId);
    if (!isOwner) {
      console.error('❌ Device not owned by user:', deviceId, userId);
      return;
    }
    
    await db.query(
      `INSERT INTO telemetry (time, device_id, gateway_id, user_id, temperature, humidity, data)
       VALUES (NOW(), $1, $2, $3, $4, $5, $6)`,
      [
        deviceId,
        gatewayId,
        userId,
        data.temperature || null,
        data.humidity || null,
        JSON.stringify(data)
      ]
    );
    
    console.log('✅ Saved telemetry:', deviceId);
  } catch (error) {
    console.error('❌ Error saving telemetry:', error);
  }
}

async function saveDeviceStatus(gatewayId, userId, deviceId, data) {
  try {
    const isOwner = await verifyDeviceOwnership(deviceId, userId);
    if (!isOwner) {
      console.error('❌ Device not owned by user:', deviceId, userId);
      return;
    }
    
    // Save to device_status table
    await db.query(
      `INSERT INTO device_status (time, device_id, gateway_id, user_id, status, sequence, metadata)
       VALUES (NOW(), $1, $2, $3, $4, $5, $6)`,
      [
        deviceId,
        gatewayId,
        userId,
        data.status || 'UNKNOWN',
        data.sequence || null,
        JSON.stringify(data)
      ]
    );
    
    // Update device last_seen and status
    await db.query(
      `UPDATE devices 
       SET status = $1, last_seen = NOW(), updated_at = NOW()
       WHERE device_id = $2`,
      [data.status || 'online', deviceId]
    );
    
    console.log('✅ Saved device status:', deviceId, data.status);
  } catch (error) {
    console.error('❌ Error saving device status:', error);
  }
}

async function saveAccessLog(gatewayId, userId, deviceId, data) {
  try {
    const isOwner = await verifyDeviceOwnership(deviceId, userId);
    if (!isOwner) {
      console.error('❌ Device not owned by user:', deviceId, userId);
      return;
    }
    
    await db.query(
      `INSERT INTO access_logs (
        time, device_id, gateway_id, user_id, method, result, 
        password_id, rfid_uid, deny_reason, metadata
      )
       VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9)`,
      [
        deviceId,
        gatewayId,
        userId,
        data.method || 'unknown',
        data.result || 'unknown',
        data.password_id || null,
        data.uid || null,
        data.deny_reason || null,
        JSON.stringify(data)
      ]
    );
    
    // Update last_used for password/rfid if access granted
    if (data.result === 'granted') {
      if (data.password_id) {
        await db.query(
          'UPDATE passwords SET last_used = NOW() WHERE password_id = $1',
          [data.password_id]
        );
      }
      if (data.uid) {
        await db.query(
          'UPDATE rfid_cards SET last_used = NOW() WHERE uid = $1',
          [data.uid]
        );
      }
    }
    
    console.log('✅ Saved access log:', deviceId, data.method, data.result);
  } catch (error) {
    console.error('❌ Error saving access log:', error);
  }
}

// Publish command to gateway
async function publishCommand(gatewayId, deviceId, command) {
  if (!client || !client.connected) {
    throw new Error('MQTT client not connected');
  }
  
  const topic = `gateway/${gatewayId}/command/${deviceId}`;
  const payload = JSON.stringify(command);
  
  return new Promise((resolve, reject) => {
    client.publish(topic, payload, { qos: 1 }, (err) => {
      if (err) {
        console.error('❌ Failed to publish command:', err);
        reject(err);
      } else {
        console.log('✅ Published command to:', topic);
        resolve();
      }
    });
  });
}

function disconnect() {
  if (client) {
    client.end();
  }
}

module.exports = {
  connect,
  disconnect,
  publishCommand,
  getClient: () => client
};