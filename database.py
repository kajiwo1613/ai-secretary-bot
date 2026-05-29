import sqlite3

DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # TODO保存用のテーブル作成
    c.execute('''CREATE TABLE IF NOT EXISTS todos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT)''')
    # 人格（Role）保存用のテーブル作成
    c.execute('''CREATE TABLE IF NOT EXISTS roles
                 (channel_id INTEGER PRIMARY KEY, role_text TEXT)''')
    conn.commit()
    conn.close()

# --- TODO機能の裏側 ---
def add_todo(user_id, task):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO todos (user_id, task) VALUES (?, ?)", (user_id, task))
    conn.commit()
    conn.close()

def get_todos(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, task FROM todos WHERE user_id = ?", (user_id,))
    todos = c.fetchall()
    conn.close()
    return todos

def delete_todo(todo_id, user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM todos WHERE id = ? AND user_id = ?", (todo_id, user_id))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

# --- Role（人格）機能の裏側 ---
def set_role(channel_id, role_text):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO roles (channel_id, role_text) VALUES (?, ?)", (channel_id, role_text))
    conn.commit()
    conn.close()

def get_role(channel_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role_text FROM roles WHERE channel_id = ?", (channel_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def delete_role(channel_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM roles WHERE channel_id = ?", (channel_id,))
    conn.commit()
    conn.close()
