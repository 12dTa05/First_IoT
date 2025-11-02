import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

class DatabaseError(Exception):
    pass

class Database:
    def __init__(self):
        self.pool = None
    
    def connect(self):
        try:
            self.pool = ThreadedConnectionPool(
                minconn=2,
                maxconn=20,
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                database=settings.DB_NAME,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD
            )
            logger.info(f'Database pool created: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}')
            
            # Test connection
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT version()')
            version = cursor.fetchone()[0]
            cursor.close()
            self.put_connection(conn)
            
            logger.info(f'Database connected: {version[:50]}...')
            return True
            
        except Exception as e:
            logger.error(f'Database connection failed: {e}')
            raise DatabaseError(f'Failed to connect to database: {e}')
    
    def get_connection(self):
        """Get connection from pool"""
        if not self.pool:
            raise DatabaseError('Database pool not initialized')
        return self.pool.getconn()
    
    def put_connection(self, conn):
        """Return connection to pool"""
        if self.pool:
            self.pool.putconn(conn)
    
    @contextmanager
    def transaction(self):
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f'Transaction rolled back: {e}')
            raise
        finally:
            self.put_connection(conn)
    
    def query(self, query_text, params=None):
        conn = self.get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query_text, params)
            
            # Fetch results if available
            if cursor.description:
                result = cursor.fetchall()
            else:
                result = []
            
            # Commit for any non-SELECT query
            query_upper = query_text.strip().upper()
            if not query_upper.startswith('SELECT'):
                conn.commit()
            
            cursor.close()
            
            if not query_upper.startswith('SELECT'):
                logger.debug(f'Query executed: {query_text[:80]}...')
            
            return result
            
        except psycopg2.IntegrityError as e:
            conn.rollback()
            logger.error(f'Integrity error: {e}')
            raise DatabaseError(f'Database integrity error: {e}')
        except psycopg2.OperationalError as e:
            conn.rollback()
            logger.error(f'Operational error: {e}')
            raise DatabaseError(f'Database operational error: {e}')
        except Exception as e:
            conn.rollback()
            logger.error(f'Query error: {e}')
            raise DatabaseError(f'Database query error: {e}')
        finally:
            self.put_connection(conn)
    
    def query_one(self, query_text, params=None):
        result = self.query(query_text, params)
        return result[0] if result and len(result) > 0 else None
    
    def execute(self, query_text, params=None):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query_text, params)
            affected_rows = cursor.rowcount
            conn.commit()
            cursor.close()
            
            logger.debug(f'Execute: {affected_rows} rows affected')
            return affected_rows
            
        except Exception as e:
            conn.rollback()
            logger.error(f'Execute error: {e}')
            raise DatabaseError(f'Database execute error: {e}')
        finally:
            self.put_connection(conn)
    
    def execute_many(self, query_text, params_list):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.executemany(query_text, params_list)
            affected_rows = cursor.rowcount
            conn.commit()
            cursor.close()
            
            logger.info(f'Bulk execute: {affected_rows} rows affected')
            return affected_rows
            
        except Exception as e:
            conn.rollback()
            logger.error(f'Execute many error: {e}')
            raise DatabaseError(f'Database bulk execute error: {e}')
        finally:
            self.put_connection(conn)
    
    def close(self):
        """Close all connections in pool"""
        if self.pool:
            self.pool.closeall()
            logger.info('Database pool closed')

# Singleton instance
db = Database()