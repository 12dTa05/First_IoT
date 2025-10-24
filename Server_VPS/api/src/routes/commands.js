const express = require('express');
const mqttService = require('../services/mqtt');
const router = express.Router();

// Send command to device via gateway
router.post('/:gateway_id/:device_id', async (req, res) => {
  try {
    const { gateway_id, device_id } = req.params;
    const { command, params } = req.body;
    
    const topic = `iot/${gateway_id}/command`;
    const message = {
      device_id,
      command,
      params,
      timestamp: new Date().toISOString()
    };
    
    mqttService.publish(topic, message);
    
    res.json({ 
      success: true, 
      message: 'Command sent',
      data: message
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Unlock door
router.post('/:gateway_id/:device_id/unlock', async (req, res) => {
  try {
    const { gateway_id, device_id } = req.params;
    const { duration = 5 } = req.body;
    
    const topic = `iot/${gateway_id}/command`;
    const message = {
      device_id,
      command: 'unlock',
      params: { duration },
      timestamp: new Date().toISOString()
    };
    
    mqttService.publish(topic, message);
    
    res.json({ 
      success: true, 
      message: 'Unlock command sent'
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Lock door
router.post('/:gateway_id/:device_id/lock', async (req, res) => {
  try {
    const { gateway_id, device_id } = req.params;
    
    const topic = `iot/${gateway_id}/command`;
    const message = {
      device_id,
      command: 'lock',
      timestamp: new Date().toISOString()
    };
    
    mqttService.publish(topic, message);
    
    res.json({ 
      success: true, 
      message: 'Lock command sent'
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;