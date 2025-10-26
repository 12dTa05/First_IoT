const express = require('express');
const db = require('../services/database');
const { authMiddleware, checkDeviceOwnership } = require('../middleware/auth');
const router = express.Router();

// Get access logs (filtered by user)
router.get('/logs', authMiddleware, async (req, res) => {
  try {
    const { user_id } = req.user;
    const { device_id, start, end, result: accessResult, limit = 100 } = req.query;
    
    let query = `
      SELECT * FROM access_logs 
      WHERE user_id = $1
    `;
    const params = [user_id];
    let paramCount = 2;
    
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
    params.push(parseInt(limit));
    
    const result = await db.query(query, params);
    res.json(result.rows);
  } catch (error) {
    console.error('Get access logs error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get RFID cards của user
router.get('/rfid', authMiddleware, async (req, res) => {
  try {
    const { user_id } = req.user;
    
    const result = await db.query(
      `SELECT * FROM rfid_cards 
       WHERE user_id = $1 
       ORDER BY registered_at DESC`,
      [user_id]
    );
    
    res.json(result.rows);
  } catch (error) {
    console.error('Get RFID cards error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Add RFID card
router.post('/rfid', authMiddleware, async (req, res) => {
  try {
    const { user_id } = req.user;
    const { uid, card_type, description, expires_at } = req.body;
    
    if (!uid) {
      return res.status(400).json({ error: 'UID required' });
    }
    
    const result = await db.query(
      `INSERT INTO rfid_cards (uid, user_id, card_type, description, expires_at)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING *`,
      [uid, user_id, card_type, description, expires_at]
    );
    
    res.status(201).json(result.rows[0]);
  } catch (error) {
    if (error.code === '23505') { // Unique violation
      return res.status(409).json({ error: 'RFID card already exists' });
    }
    console.error('Add RFID card error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Update RFID card
router.put('/rfid/:uid', authMiddleware, async (req, res) => {
  try {
    const { user_id } = req.user;
    const { uid } = req.params;
    const { active, description, expires_at } = req.body;
    
    // Check ownership
    const checkResult = await db.query(
      'SELECT 1 FROM rfid_cards WHERE uid = $1 AND user_id = $2',
      [uid, user_id]
    );
    
    if (checkResult.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }
    
    const result = await db.query(
      `UPDATE rfid_cards 
       SET active = COALESCE($1, active),
           description = COALESCE($2, description),
           expires_at = COALESCE($3, expires_at),
           updated_at = NOW()
       WHERE uid = $4 AND user_id = $5
       RETURNING *`,
      [active, description, expires_at, uid, user_id]
    );
    
    res.json(result.rows[0]);
  } catch (error) {
    console.error('Update RFID card error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get passwords của user
router.get('/passwords', authMiddleware, async (req, res) => {
  try {
    const { user_id } = req.user;
    
    const result = await db.query(
      `SELECT password_id, description, active, created_at, last_used, expires_at 
       FROM passwords 
       WHERE user_id = $1 
       ORDER BY created_at DESC`,
      [user_id]
    );
    
    res.json(result.rows);
  } catch (error) {
    console.error('Get passwords error:', error);
    res.status(500).json({ error: error.message });
  }
});

// Add password
router.post('/passwords', authMiddleware, async (req, res) => {
  try {
    const { user_id } = req.user;
    const bcrypt = require('bcrypt');
    const { password_id, password, description, expires_at } = req.body;
    
    if (!password_id || !password) {
      return res.status(400).json({ error: 'Password ID and password required' });
    }
    
    const hash = await bcrypt.hash(password, 10);
    
    const result = await db.query(
      `INSERT INTO passwords (password_id, user_id, hash, description, expires_at)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING password_id, description, active, created_at, expires_at`,
      [password_id, user_id, hash, description, expires_at]
    );
    
    res.status(201).json(result.rows[0]);
  } catch (error) {
    if (error.code === '23505') {
      return res.status(409).json({ error: 'Password ID already exists' });
    }
    console.error('Add password error:', error);
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;