import sqlite3
from datetime import datetime

DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS roles (channel_id INTEGER PRIMARY KEY, role_text TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage_logs (user_id INTEGER, date TEXT, count INTEGER, PRIMARY KEY(user_id, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT UNIQUE, content TEXT)''')
    conn.commit()
    conn.close()

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

def check_and_increment_usage(user_id, limit=20):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT count FROM usage_logs WHERE user_id = ? AND date = ?", (user_id, today))
    row = c.fetchone()
    if row:
        count = row[0]
        if count >= limit:
            conn.close()
            return False, count
        c.execute("UPDATE usage_logs SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
        new_count = count + 1
    else:
        c.execute("INSERT INTO usage_logs (user_id, date, count) VALUES (?, ?, 1)", (user_id, today))
        new_count = 1
    conn.commit()
    conn.close()
    return True, new_count

def add_knowledge(keyword, content):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO knowledge (keyword, content) VALUES (?, ?)", (keyword, content))
    conn.commit()
    conn.close()

def search_knowledge(text):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT keyword, content FROM knowledge")
    rows = c.fetchall()
    conn.close()
    matched_context = ""
    for keyword, content in rows:
        if keyword in text:
            matched_context += f"【登録知識: {keyword} に関する公式論証・条文】\n{content}\n\n"
    return matched_context
