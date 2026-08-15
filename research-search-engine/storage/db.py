
# storage/db.py
import sqlite3

def init_db(db_name="papers.db"):
    conn = sqlite3.connect(db_name) # using sqlite model and connect it to db_name
    cursor = conn.cursor()  #creating table
    cursor.execute('''   
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,
            doi TEXT,
            title TEXT,
            authors TEXT,
            abstract TEXT,
            year INTEGER
        )
    ''')
    conn.commit() #saving stuff
    conn.close()
    print("Database initialized successfully!")
    
if __name__ == "__main__":
    init_db()
