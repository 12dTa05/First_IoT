const express = require('express');
const db = require('../services/database');
const router = express.Router();

// Get all gateways
router.get('/', async (req, res) => {
  try {
    const result = await db.query(
      'SELECT * FROM gateways ORDER BY created_at DESC'
    );
    res.json(result.rows);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Get gateway by ID
router.get('/:id', async (req, res) => {
  try {
    const result = await db.query(
      'SELECT * FROM gateways WHERE gateway_id = $1',
      [req.params.id]
    );
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Gateway not found' });
    }
    res.json(result.rows[0]);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Get gateway devices
router.get('/:id/devices', async (req, res) => {
  try {
    const result = await db.query(
      'SELECT * FROM devices WHERE gateway_id = $1',
      [req.params.id]
    );
    res.json(result.rows);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;