const express = require('express');
const db = require('../services/database');
const router = express.Router();

// Get all devices
router.get('/', async (req, res) => {
  try {
    const result = await db.query(`
      SELECT d.*, g.name as gateway_name 
      FROM devices d
      LEFT JOIN gateways g ON d.gateway_id = g.gateway_id
      ORDER BY d.created_at DESC
    `);
    res.json(result.rows);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Get device by ID
router.get('/:id', async (req, res) => {
  try {
    const result = await db.query(
      'SELECT * FROM devices WHERE device_id = $1',
      [req.params.id]
    );
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Device not found' });
    }
    res.json(result.rows[0]);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Update device
router.put('/:id', async (req, res) => {
  try {
    const { location, status, firmware_version } = req.body;
    const result = await db.query(
      `UPDATE devices 
       SET location = COALESCE($1, location),
           status = COALESCE($2, status),
           firmware_version = COALESCE($3, firmware_version)
       WHERE device_id = $4
       RETURNING *`,
      [location, status, firmware_version, req.params.id]
    );
    res.json(result.rows[0]);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Get device health
router.get('/:id/health', async (req, res) => {
  try {
    const result = await db.query(
      'SELECT * FROM device_health WHERE device_id = $1',
      [req.params.id]
    );
    res.json(result.rows[0] || {});
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;