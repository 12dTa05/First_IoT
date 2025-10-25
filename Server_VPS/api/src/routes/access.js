const express = require('express');
const crypto = require('crypto');
const db = require('../services/database');
const router = express.Router();

// FIXED: Use SHA-256 with salt to match Gateway's authentication
// This ensures compatibility between Gateway local auth and VPS database
const DEVICE_SALT = 'IoTSmartHome2024SecureSalt!@#';

function hashPassword(password) {
  // Match Gateway's password hashing: SHA-256(password + salt)
  const hash = crypto.createHash('sha256');
  hash.update(password + DEVICE_SALT);
  return hash.digest('hex');
}

// Get access logs
router.get('/logs', async (req, res) => {
  try {
    const { device_id, start, end, result: accessResult, method, limit = 100 } = req.query;
    
    let query = 'SELECT * FROM access_logs WHERE 1=1';
    const params = [];
    let paramCount = 1;
    
    if (device_id) {
      query += ` AND device_id = $${paramCount}`;
      params.push(device_id);
      paramCount++;
    }
    
    if (method) {
      query += ` AND method = $${paramCount}`;
      params.push(method);
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
    
    if (accessResult) {
      query += ` AND result = $${paramCount}`;
      params.push(accessResult);
      paramCount++;
    }
    
    query += ` ORDER BY time DESC LIMIT $${paramCount}`;
    params.push(limit);
    
    const result = await db.query(query, params);
    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching access logs:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get RFID cards
router.get('/rfid', async (req, res) => {
  try {
    const result = await db.query(
      'SELECT * FROM rfid_cards ORDER BY registered_at DESC'
    );
    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching RFID cards:', error);
    res.status(500).json({ error: error.message });
  }
});

// Add RFID card
router.post('/rfid', async (req, res) => {
  try {
    const { uid, owner, card_type, description, expires_at, active = true } = req.body;
    
    if (!uid || !owner) {
      return res.status(400).json({ error: 'uid and owner are required' });
    }
    
    const result = await db.query(
      `INSERT INTO rfid_cards (uid, active, owner, card_type, description, registered_at, expires_at)
       VALUES ($1, $2, $3, $4, $5, NOW(), $6)
       RETURNING *`,
      [uid, active, owner, card_type, description, expires_at]
    );
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error adding RFID card:', error);
    res.status(500).json({ error: error.message });
  }
});

// Update RFID card status
router.patch('/rfid/:uid', async (req, res) => {
  try {
    const { uid } = req.params;
    const { active, deactivation_reason } = req.body;
    
    const result = await db.query(
      `UPDATE rfid_cards 
       SET active = $1, 
           deactivated_at = CASE WHEN $1 = false THEN NOW() ELSE deactivated_at END,
           deactivation_reason = CASE WHEN $1 = false THEN $2 ELSE deactivation_reason END
       WHERE uid = $3
       RETURNING *`,
      [active, deactivation_reason, uid]
    );
    
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'RFID card not found' });
    }
    
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error updating RFID card:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get passwords
router.get('/passwords', async (req, res) => {
  try {
    const result = await db.query(
      'SELECT password_id, owner, description, active, created_at, last_used, expires_at FROM passwords ORDER BY created_at DESC'
    );
    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching passwords:', error);
    res.status(500).json({ error: error.message });
  }
});

// Add password - FIXED to use SHA-256 matching Gateway
router.post('/passwords', async (req, res) => {
  try {
    const { password_id, password, owner, description, expires_at, active = true } = req.body;
    
    if (!password_id || !password || !owner) {
      return res.status(400).json({ error: 'password_id, password, and owner are required' });
    }
    
    // Use SHA-256 hash to match Gateway's authentication method
    const hash = hashPassword(password);
    
    const result = await db.query(
      `INSERT INTO passwords (password_id, hash, active, owner, description, created_at, expires_at)
       VALUES ($1, $2, $3, $4, $5, NOW(), $6)
       RETURNING password_id, owner, description, active, created_at, expires_at`,
      [password_id, hash, active, owner, description, expires_at]
    );
    
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error adding password:', error);
    res.status(500).json({ error: error.message });
  }
});

// Update password status
router.patch('/passwords/:password_id', async (req, res) => {
  try {
    const { password_id } = req.params;
    const { active } = req.body;
    
    const result = await db.query(
      `UPDATE passwords 
       SET active = $1
       WHERE password_id = $2
       RETURNING password_id, owner, description, active, created_at, expires_at`,
      [active, password_id]
    );
    
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Password not found' });
    }
    
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error updating password:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get access statistics
router.get('/stats', async (req, res) => {
  try {
    const { days = 7 } = req.query;
    
    const result = await db.query(
      `SELECT 
        DATE(time) as date,
        method,
        result,
        COUNT(*) as count
       FROM access_logs
       WHERE time >= NOW() - INTERVAL '${parseInt(days)} days'
       GROUP BY DATE(time), method, result
       ORDER BY date DESC, method, result`
    );
    
    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching access stats:', error);
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;