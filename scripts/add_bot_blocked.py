import sqlite3
import os

def migrate():
    db_path = os.path.join(os.path.dirname(__file__), '..', 'data.db')
    log_path = os.path.join(os.path.dirname(__file__), 'migration_log.txt')
    
    with open(log_path, 'w') as log:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Check if column exists
            cursor.execute("PRAGMA table_info(users)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'bot_blocked' not in columns:
                log.write("Adding bot_blocked column to users table...\n")
                cursor.execute("ALTER TABLE users ADD COLUMN bot_blocked BOOLEAN DEFAULT 0")
                conn.commit()
                log.write("Column added successfully.\n")
            else:
                log.write("Column bot_blocked already exists.\n")
            
            conn.close()
        except Exception as e:
            log.write(f"Error during migration: {str(e)}\n")

if __name__ == "__main__":
    migrate()
