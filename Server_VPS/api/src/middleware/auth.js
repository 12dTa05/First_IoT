const jwt = require('jsonwebtoken');
const db = require('../services/database');

// Middleware xác thực token
function authMiddleware(req, res, next) {
  const token = req.headers.authorization?.split(' ')[1];

  if (!token) {
    return res.status(401).json({ error: 'No token provided' });
  }

  try {
    const decoded = jwt.verify(token, process.env.JWT_SECRET);
    req.user = decoded; // { user_id, username, role }
    next();
  } catch (error) {
    return res.status(401).json({ error: 'Invalid or expired token' });
  }
}

// Middleware kiểm tra quyền sở hữu device
async function checkDeviceOwnership(req, res, next) {
  try {
    const { device_id } = req.params;
    const { user_id } = req.user;
    
    if (!device_id) {
      return res.status(400).json({ error: 'Device ID required' });
    }
    
    const result = await db.query(
      'SELECT 1 FROM devices WHERE device_id = $1 AND user_id = $2',
      [device_id, user_id]
    );
    
    if (result.rows.length === 0) {
      return res.status(403).json({ 
        error: 'Access denied: You do not own this device' 
      });
    }
    
    next();
  } catch (error) {
    console.error('Device ownership check error:', error);
    res.status(500).json({ error: error.message });
  }
}

// Middleware kiểm tra quyền sở hữu gateway
async function checkGatewayOwnership(req, res, next) {
  try {
    const { gateway_id } = req.params;
    const { user_id } = req.user;
    
    if (!gateway_id) {
      return res.status(400).json({ error: 'Gateway ID required' });
    }
    
    const result = await db.query(
      'SELECT 1 FROM gateways WHERE gateway_id = $1 AND user_id = $2',
      [gateway_id, user_id]
    );
    
    if (result.rows.length === 0) {
      return res.status(403).json({ 
        error: 'Access denied: You do not own this gateway' 
      });
    }
    
    next();
  } catch (error) {
    console.error('Gateway ownership check error:', error);
    res.status(500).json({ error: error.message });
  }
}

// Middleware kiểm tra role admin (optional)
function requireAdmin(req, res, next) {
  if (req.user.role !== 'admin') {
    return res.status(403).json({ error: 'Admin access required' });
  }
  next();
}

module.exports = {
  authMiddleware,
  checkDeviceOwnership,
  checkGatewayOwnership,
  requireAdmin
};