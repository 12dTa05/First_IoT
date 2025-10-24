const express = require('express');
const db = require('../services/database');
const router = express.Router();

// Get telemetry data
router.get('/', async (req, res) => {
  try {
    const { device_id, start, end, limit = 100 } = req.query;
    
    let query = 'SELECT * FROM telemetry WHERE 1=1';
    const params = [];
    let paramCount = 1;
    
    if (device_id) {
      query += ` AND device_id = $${paramCount}`;
      params.push(device_id);
      paramCount++;
    }
    
    if (start) {
      query += ` AND time >= $${paramCount}`;
      params.push(start);
      paramCount++;
    }
    
    if (end) {
      query += ` AND time <= $${paramCount}`;
      params.push(end);
      paramCount++;
    }
    
    query += ` ORDER BY time DESC LIMIT $${paramCount}`;
    params.push(limit);
    
    const result = await db.query(query, params);
    res.json(result.rows);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Get latest telemetry for device
router.get('/latest/:device_id', async (req, res) => {
  try {
    const result = await db.query(
      `SELECT * FROM telemetry 
       WHERE device_id = $1 
       ORDER BY time DESC LIMIT 1`,
      [req.params.device_id]
    );
    res.json(result.rows[0] || {});
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Get hourly aggregates
router.get('/hourly', async (req, res) => {
  try {
    const { device_id, days = 7 } = req.query;
    
    let query = `
      SELECT * FROM telemetry_hourly 
      WHERE bucket >= NOW() - INTERVAL '${days} days'
    `;
    
    if (device_id) {
      query += ` AND device_id = $1`;
      const result = await db.query(query, [device_id]);
      return res.json(result.rows);
    }
    
    const result = await db.query(query);
    res.json(result.rows);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;