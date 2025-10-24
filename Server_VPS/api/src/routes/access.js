const express = require('express');
const db = require('../services/database');
const router = express.Router();

// Get access logs
router.get('/logs', async (req, res) => {
  try {
    const { device_id, start, end, result: accessResult, limit = 100 } = req.query;
    
    let query = 'SELECT * FROM access_logs WHERE 1=1';
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
    res.status(500).json({ error: error.message });
  }
});

// Add RFID card
router.post('/rfid', async (req, res) => {
  try {
    const { uid, owner, card_type, description, expires_at } = req.body;
    const result = await db.query(
      `INSERT INTO rfid_cards (uid, owner, card_type, description, registered_at, expires_at)
       VALUES ($1, $2, $3, $4, NOW(), $5)
       RETURNING *`,
      [uid, owner, card_type, description, expires_at]
    );
    res.json(result.rows[0]);
  } catch (error) {
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
    res.status(500).json({ error: error.message });
  }
});

// Add password
router.post('/passwords', async (req, res) => {
  try {
    const bcrypt = require('bcrypt');
    const { password_id, password, owner, description, expires_at } = req.body;
    const hash = await bcrypt.hash(password, 10);
    
    const result = await db.query(
      `INSERT INTO passwords (password_id, hash, owner, description, created_at, expires_at)
       VALUES ($1, $2, $3, $4, NOW(), $5)
       RETURNING password_id, owner, description, active, created_at, expires_at`,
      [password_id, hash, owner, description, expires_at]
    );
    res.json(result.rows[0]);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;