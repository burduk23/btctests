import sqlite3
import os

def verify():
    db_path = os.path.join(os.path.dirname(__file__), '..', 'data.db')
    log_path = os.path.join(os.path.dirname(__file__), 'verify_log.txt')
    
    with open(log_path, 'w') as log:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            cursor.execute("PRAGMA table_info(users)")
            columns = cursor.fetchall()
            log.write("Users table columns:\n")
            for col in columns:
                log.write(f"{col}\n")
            
            conn.close()
        except Exception as e:
            log.write(f"Error during verification: {str(e)}\n")

if __name__ == "__main__":
    verify()
