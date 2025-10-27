require('dotenv').config();
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const rateLimit = require('express-rate-limit');

const db = require('./services/database');
const mqttService = require('./services/mqtt');
const authMiddleware = require('./middleware/auth');

// Routes
const devicesRouter = require('./routes/devices');
const telemetryRouter = require('./routes/telemetry');
const accessRouter = require('./routes/access');
const gatewaysRouter = require('./routes/gateways');
const commandRouter = require('./routes/commands');
const authRouter = require('./routes/auth');

const app = express();
const PORT = process.env.API_PORT || 3000;

app.set('trust proxy', 1);

// Middleware
app.use(helmet());
app.use(cors());
app.use(express.json());

// Rate limiting
const limiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 100
});
app.use(limiter);

// Health check
app.get('/health', (req, res) => {
  res.status(200).json({ 
    status: 'healthy', 
    timestamp: new Date().toISOString(),
    uptime: process.uptime()
  });
});

// Public routes
app.use('/api/auth', authRouter);

// Protected routes
app.use('/api/devices', authMiddleware, devicesRouter);
app.use('/api/telemetry', authMiddleware, telemetryRouter);
app.use('/api/access', authMiddleware, accessRouter);
app.use('/api/gateways', authMiddleware, gatewaysRouter);
app.use('/api/commands', authMiddleware, commandRouter);

// Error handler
app.use((err, req, res, next) => {
  console.error('Error:', err);
  res.status(err.status || 500).json({
    error: err.message || 'Internal server error'
  });
});

// Start server
async function start() {
  try {
    await db.connect();
    await mqttService.connect();
    
    app.listen(PORT, '0.0.0.0', () => {
      console.log(`API Server running on port ${PORT}`);
    });
  } catch (error) {
    console.error('Failed to start server:', error);
    process.exit(1);
  }
}

start();