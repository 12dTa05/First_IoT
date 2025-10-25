const mqtt = require('mqtt');
const db = require('./database');

let client = null;

const TOPICS = {
  TELEMETRY: 'iot/+/telemetry',
  STATUS: 'iot/+/status',
  ACCESS: 'iot/+/access',
  ALERT: 'iot/+/alert',
  GATEWAY_STATUS: 'gateway/status/+'
};

async function connect() {
  const options = {
    username: process.env.MQTT_USERNAME || 'gateway',
    password: process.env.MQTT_PASSWORD || '2003',
    clientId: 'iot-api-server',
    clean: true,
    reconnectPeriod: 5000
  };

  const mqttHost = process.env.MQTT_HOST || 'localhost';
  const mqttPort = process.env.MQTT_PORT || 1883;

  console.log(`[MQTT] Connecting to mqtt://${mqttHost}:${mqttPort}`);
  console.log(`[MQTT] Client ID: ${options.clientId}`);
  console.log(`[MQTT] Username: ${options.username}`);

  client = mqtt.connect(`mqtt://${mqttHost}:${mqttPort}`, options);

  client.on('connect', () => {
    console.log('✓ MQTT connected to broker');
    
    const topicsArray = Object.values(TOPICS);
    console.log('[MQTT] Subscribing to topics:', topicsArray);
    
    client.subscribe(topicsArray, { qos: 1 }, (err) => {
      if (err) {
        console.error('✗ MQTT subscribe error:', err);
      } else {
        console.log('✓ Successfully subscribed to all topics');
      }
    });
  });

  client.on('message', handleMessage);
  
  client.on('error', (err) => {
    console.error('✗ MQTT error:', err);
  });
  
  client.on('reconnect', () => {
    console.log('→ MQTT reconnecting...');
  });
  
  client.on('disconnect', () => {
    console.log('✗ MQTT disconnected');
  });
}

async function handleMessage(topic, message) {
  const timestamp = new Date().toISOString();
  
  console.log(`\n[${timestamp}] ========================================`);
  console.log(`[MQTT RECEIVED] Topic: ${topic}`);
  console.log(`[MQTT RECEIVED] Payload length: ${message.length} bytes`);
  
  try {
    const data = JSON.parse(message.toString());
    const parts = topic.split('/');
    
    console.log(`[MQTT RECEIVED] Parsed JSON successfully`);
    console.log(`[MQTT RECEIVED] Topic parts:`, parts);
    
    if (parts.length < 3) {
      console.error('[MQTT ERROR] Invalid topic structure:', topic);
      return;
    }
    
    const gatewayId = parts[1];
    const type = parts[2];

    console.log(`[MQTT RECEIVED] Gateway: ${gatewayId}, Type: ${type}`);
    console.log(`[MQTT RECEIVED] Device: ${data.device_id || 'unknown'}`);
    console.log(`[MQTT RECEIVED] Full data:`, JSON.stringify(data, null, 2));

    switch (type) {
      case 'telemetry':
        console.log(`[PROCESSING] Saving telemetry for ${data.device_id}...`);
        await saveTelemetry(data);
        console.log(`[SUCCESS] ✓ Telemetry saved for ${data.device_id}`);
        break;
        
      case 'status':
        console.log(`[PROCESSING] Saving status for ${data.device_id}...`);
        await saveStatus(data);
        console.log(`[SUCCESS] ✓ Status saved for ${data.device_id}`);
        break;
        
      case 'access':
        console.log(`[PROCESSING] Saving access log for ${data.device_id}...`);
        await saveAccess(data);
        console.log(`[SUCCESS] ✓ Access log saved for ${data.device_id}`);
        break;
        
      case 'alert':
        console.log(`[PROCESSING] Saving alert for ${data.device_id}...`);
        await saveAlert(data);
        console.log(`[SUCCESS] ✓ Alert saved for ${data.device_id}`);
        break;
        
      default:
        console.log(`[WARNING] Unknown message type: ${type}`);
    }
    
    console.log(`[${timestamp}] ========================================\n`);
    
  } catch (error) {
    console.error(`\n[${timestamp}] ========================================`);
    console.error('[ERROR] MQTT message handler error:', error.message);
    console.error('[ERROR] Error stack:', error.stack);
    console.error('[ERROR] Topic:', topic);
    console.error('[ERROR] Raw message:', message.toString().substring(0, 500));
    console.error(`========================================\n`);
  }
}

async function saveTelemetry(data) {
  try {
    console.log('[DB] Preparing telemetry insert query');
    console.log('[DB] Values:', {
      device_id: data.device_id,
      gateway_id: data.gateway_id,
      temperature: data.temperature,
      humidity: data.humidity
    });
    
    const query = `
      INSERT INTO telemetry (time, device_id, gateway_id, temperature, humidity, data)
      VALUES (NOW(), $1, $2, $3, $4, $5)
    `;
    
    await db.query(query, [
      data.device_id,
      data.gateway_id,
      data.temperature || null,
      data.humidity || null,
      data.data || data
    ]);
    
    console.log(`[DB] ✓ Telemetry insert successful`);
    
  } catch (error) {
    console.error('[DB ERROR] Failed to save telemetry:', error.message);
    console.error('[DB ERROR] Error details:', error);
    throw error;
  }
}

async function saveStatus(data) {
  try {
    console.log('[DB] Preparing status insert query');
    console.log('[DB] Values:', {
      device_id: data.device_id,
      gateway_id: data.gateway_id,
      status: data.status
    });
    
    const statusQuery = `
      INSERT INTO device_status (time, device_id, gateway_id, status, sequence, metadata)
      VALUES (NOW(), $1, $2, $3, $4, $5)
    `;
    
    await db.query(statusQuery, [
      data.device_id,
      data.gateway_id,
      data.status || 'unknown',
      data.sequence || null,
      data.metadata || data
    ]);
    
    console.log(`[DB] ✓ Status insert successful`);
    
    const updateQuery = `
      UPDATE devices 
      SET status = $1, last_seen = NOW() 
      WHERE device_id = $2
    `;
    
    await db.query(updateQuery, [
      data.status || 'unknown',
      data.device_id
    ]);
    
    console.log(`[DB] ✓ Device table updated`);
    
  } catch (error) {
    console.error('[DB ERROR] Failed to save status:', error.message);
    console.error('[DB ERROR] Error details:', error);
    throw error;
  }
}

async function saveAccess(data) {
  try {
    console.log('[DB] Preparing access log insert query');
    console.log('[DB] Values:', {
      device_id: data.device_id,
      gateway_id: data.gateway_id,
      method: data.method,
      result: data.result,
      rfid_uid: data.rfid_uid
    });
    
    const query = `
      INSERT INTO access_logs (
        time, device_id, gateway_id, method, result, 
        password_id, rfid_uid, deny_reason, metadata
      )
      VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8)
    `;
    
    await db.query(query, [
      data.device_id,
      data.gateway_id,
      data.method || 'unknown',
      data.result || 'unknown',
      data.password_id || null,
      data.rfid_uid || null,
      data.deny_reason || null,
      data.metadata || data
    ]);
    
    console.log(`[DB] ✓ Access log insert successful`);
    
  } catch (error) {
    console.error('[DB ERROR] Failed to save access log:', error.message);
    console.error('[DB ERROR] Error details:', error);
    console.error('[DB ERROR] SQL State:', error.code);
    throw error;
  }
}

async function saveAlert(data) {
  try {
    console.log('[DB] Preparing alert insert query');
    console.log('[DB] Values:', {
      device_id: data.device_id,
      gateway_id: data.gateway_id,
      alert_type: data.alert_type,
      severity: data.severity
    });
    
    const query = `
      INSERT INTO alerts (
        time, device_id, gateway_id, alert_type, severity, 
        value, threshold, message, metadata
      )
      VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8)
    `;
    
    await db.query(query, [
      data.device_id,
      data.gateway_id,
      data.alert_type || 'unknown',
      data.severity || 'warning',
      data.value || null,
      data.threshold || null,
      data.message || '',
      data.metadata || data
    ]);
    
    console.log(`[DB] ✓ Alert insert successful`);
    
  } catch (error) {
    console.error('[DB ERROR] Failed to save alert:', error.message);
    console.error('[DB ERROR] Error details:', error);
    throw error;
  }
}

function publish(topic, message) {
  if (client && client.connected) {
    client.publish(topic, JSON.stringify(message), { qos: 1 });
    console.log(`[MQTT] Published to ${topic}`);
  } else {
    console.error('✗ Cannot publish: MQTT client not connected');
  }
}

function disconnect() {
  if (client) {
    client.end();
    console.log('[MQTT] Client disconnected');
  }
}

module.exports = {
  connect,
  publish,
  disconnect,
  TOPICS
};