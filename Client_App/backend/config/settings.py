import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Backend BFF settings
    BFF_HOST = os.getenv('BFF_HOST', '0.0.0.0')
    BFF_PORT = int(os.getenv('BFF_PORT', 8090))
    
    # VPS API Server connection
    VPS_API_URL = os.getenv('VPS_API_URL', 'http://localhost:3000')
    VPS_WS_URL = os.getenv('VPS_WS_URL', 'ws://localhost:3000')
    
    # Session management
    SESSION_SECRET = os.getenv('SESSION_SECRET', 'your-session-secret-key-change-in-production')
    SESSION_COOKIE_NAME = 'iot_session'
    SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
    
    # CORS settings
    FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:3001')
    CORS_ORIGINS = [
        'https://orange-doodle-5wx965q4p56cpp6j-3001.app.github.dev',
        'http://localhost:3001',
        'http://127.0.0.1:3001',
        'http://localhost:3000',  # Add if needed
        'http://127.0.0.1:3000',
    ]
    
    # Add CORS credentials
    CORS_ALLOW_CREDENTIALS = True
    
    # Request timeout
    API_TIMEOUT = int(os.getenv('API_TIMEOUT', 30))
    
    # Environment
    ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
    DEBUG = os.getenv('DEBUG', 'true').lower() == 'true'

settings = Settings()