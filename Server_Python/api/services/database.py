import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None
    
    def connect(self):
        try:
            self.pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=20,
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                database=settings.DB_NAME,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD
            )
            logger.info('Database connected')
            return True
        except Exception as e:
            logger.error(f'Database connection failed: {e}')
            raise e
    
    def get_connection(self):
        return self.pool.getconn()
    
    def put_connection(self, conn):
        self.pool.putconn(conn)
    
    def query(self, query_text, params=None):
        conn = self.get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query_text, params)
            
            if query_text.strip().upper().startswith('SELECT'):
                result = cursor.fetchall()
            else:
                conn.commit()
                result = cursor.fetchall() if cursor.description else []
            
            cursor.close()
            logger.info(f'Executed query: {query_text[:100]}...')
            return result
        except Exception as e:
            conn.rollback()
            logger.error(f'Query error: {e}')
            raise e
        finally:
            self.put_connection(conn)
    
    def close(self):
        if self.pool:
            self.pool.closeall()
            logger.info('Database pool closed')

db = Database()