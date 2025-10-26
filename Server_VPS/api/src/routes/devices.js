const express = require('express');
const db = require('../services/database');
const { authMiddleware, checkDeviceOwnership } = require('../middleware/auth');
const router = express.Router();

// Get all devices của user
router.get('/', authMiddleware, async (req, res) => {
  try {
    const { user_id } = req.user;
    
    const result = await db.query(
      `SELECT d.*, g.name AS gateway_name, g.status AS gateway_status
       FROM devices d
       JOIN gateways g ON d.gateway_id = g.gateway_id
       WHERE d.user_id = $1
       ORDER BY d.created_at DESC`,
      [user_id]
    );
    
    res.json(result.rows);
  } catch (error) {
    console.error('Get devices error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get device by ID (với ownership check)
router.get('/:device_id', authMiddleware, checkDeviceOwnership, async (req, res) => {
  try {
    const { device_id } = req.params;
    
    const result = await db.query(
      `SELECT d.*, g.name AS gateway_name, g.status AS gateway_status
       FROM devices d
       JOIN gateways g ON d.gateway_id = g.gateway_id
       WHERE d.device_id = $1`,
      [device_id]
    );
    
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Device not found' });
    }
    
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Get device error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Update device
router.put('/:device_id', authMiddleware, checkDeviceOwnership, async (req, res) => {
  try {
    const { device_id } = req.params;
    const { location, metadata } = req.body;
    
    const result = await db.query(
      `UPDATE devices 
       SET location = COALESCE($1, location),
           metadata = COALESCE($2, metadata),
           updated_at = NOW()
       WHERE device_id = $3
       RETURNING *`,
      [location, metadata ? JSON.stringify(metadata) : null, device_id]
    );
    
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Update device error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get device health
router.get('/:device_id/health', authMiddleware, checkDeviceOwnership, async (req, res) => {
  try {
    const { device_id } = req.params;
    
    const result = await db.query(
      `SELECT * FROM device_health_view WHERE device_id = $1`,
      [device_id]
    );
    
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Device not found' });
    }
    
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Get device health error:', error);
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;