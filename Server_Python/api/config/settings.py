import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    DB_HOST = os.getenv('DB_HOST', 'postgres')
    DB_PORT = int(os.getenv('DB_PORT', 5432))
    DB_NAME = os.getenv('DB_NAME', 'iot_db')
    DB_USER = os.getenv('DB_USER', 'iot')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '2003')
    
    MQTT_HOST = os.getenv('MQTT_HOST', 'mosquitto')
    MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
    MQTT_USERNAME = os.getenv('MQTT_USERNAME', 'gateway')
    MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '2003')
    
    API_PORT = int(os.getenv('API_PORT', 3000))
    JWT_SECRET = os.getenv('JWT_SECRET', 'ThaiVuongMinhThaoLinhTu@2003')
    JWT_ALGORITHM = 'HS256'
    JWT_EXPIRATION_DAYS = 7
    
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'info')

settings = Settings()