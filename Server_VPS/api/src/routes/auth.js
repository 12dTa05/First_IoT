const express = require('express');
const jwt = require('jsonwebtoken');
const bcrypt = require('bcrypt');
const router = express.Router();

// Simple login - tạo user trong DB trước hoặc hardcode để test
router.post('/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    
    // Hardcode cho đơn giản - production nên lưu trong DB
    if (username === 'admin' && password === 'admin123') {
      const token = jwt.sign(
        { username, role: 'admin' },
        process.env.JWT_SECRET,
        { expiresIn: '7d' }
      );
      
      return res.json({ token, username });
    }
    
    res.status(401).json({ error: 'Invalid credentials' });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;