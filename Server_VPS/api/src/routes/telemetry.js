const express = require('express');
const db = require('../services/database');
const { authMiddleware, checkDeviceOwnership } = require('../middleware/auth');
const router = express.Router();

// Get telemetry data (filtered by user)
router.get('/', authMiddleware, async (req, res) => {
  try {
    const { user_id } = req.user;
    const { device_id, start, end, limit = 100 } = req.query;
    
    let query = `
      SELECT t.* FROM telemetry t
      WHERE t.user_id = $1
    `;
    const params = [user_id];
    let paramCount = 2;
    
    if (device_id) {
      query += ` AND t.device_id = $${paramCount}`;
      params.push(device_id);
      paramCount++;
    }
    
    if (start) {
      query += ` AND t.time >= $${paramCount}`;
      params.push(start);
      paramCount++;
    }
    
    if (end) {
      query += ` AND t.time <= $${paramCount}`;
      params.push(end);
      paramCount++;
    }
    
    query += ` ORDER BY t.time DESC LIMIT $${paramCount}`;
    params.push(parseInt(limit));
    
    const result = await db.query(query, params);
    res.json(result.rows);
  } catch (error) {
    console.error('Get telemetry error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get latest telemetry for a device
router.get('/latest/:device_id', authMiddleware, checkDeviceOwnership, async (req, res) => {
  try {
    const { device_id } = req.params;
    
    const result = await db.query(
      `SELECT * FROM telemetry 
       WHERE device_id = $1 
       ORDER BY time DESC 
       LIMIT 1`,
      [device_id]
    );
    
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'No telemetry data found' });
    }
    
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Get latest telemetry error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get aggregated telemetry (hourly average)
router.get('/aggregate/:device_id', authMiddleware, checkDeviceOwnership, async (req, res) => {
  try {
    const { device_id } = req.params;
    const { start, end, interval = '1 hour' } = req.query;
    
    let query = `
      SELECT 
        time_bucket($1, time) AS bucket,
        AVG(temperature) AS avg_temperature,
        AVG(humidity) AS avg_humidity,
        MIN(temperature) AS min_temperature,
        MAX(temperature) AS max_temperature,
        COUNT(*) AS sample_count
      FROM telemetry
      WHERE device_id = $2
    `;
    const params = [interval, device_id];
    let paramCount = 3;
    
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
    
    query += ` GROUP BY bucket ORDER BY bucket DESC`;
    
    const result = await db.query(query, params);
    res.json(result.rows);
  } catch (error) {
    console.error('Get aggregated telemetry error:', error);
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;