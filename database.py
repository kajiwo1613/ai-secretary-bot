import sqlite3
from datetime import datetime

DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 期限・優先度・通知状態を管理するカラムを追加した拡張版テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    user_id INTEGER, 
                    task TEXT,
                    deadline TEXT,
                    priority TEXT,
                    reminded_24h INTEGER DEFAULT 0,
                    reminded_3h INTEGER DEFAULT 0
                 )''')
    c.execute('''CREATE TABLE IF NOT EXISTS roles (channel_id INTEGER PRIMARY KEY, role_text TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage_logs (user_id INTEGER, date TEXT, count INTEGER, PRIMARY KEY(user_id, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT UNIQUE, content TEXT)''')
    
    # 既存の古いデータベースがある場合への互換性維持（カラムの追加）
    try:
        c.execute("ALTER TABLE todos ADD COLUMN deadline TEXT")
    except sqlite3.OperationalError: pass
    try:
        c.execute("ALTER TABLE todos ADD COLUMN priority TEXT")
    except sqlite3.OperationalError: pass
    try:
        c.execute("ALTER TABLE todos ADD COLUMN reminded_24h INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try:
        c.execute("ALTER TABLE todos ADD COLUMN reminded_3h INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass

    conn.commit()
    conn.close()

def add_todo(user_id, task, deadline=None, priority="中"):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO todos (user_id, task, deadline, priority) VALUES (?, ?, ?, ?)", (user_id, task, deadline, priority))
    conn.commit()
    conn.close()

def get_todos(user_id, filter_type="all"):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    base_query = "SELECT id, task, deadline, priority FROM todos WHERE user_id = ?"
    order_query = " ORDER BY CASE priority WHEN '高' THEN 1 WHEN '中' THEN 2 WHEN '低' THEN 3 ELSE 4 END, id ASC"
    
    if filter_type == "today":
        c.execute(base_query + " AND deadline = ?" + order_query, (user_id, today_str))
    elif filter_type == "3days":
        c.execute(base_query + " AND deadline >= date('now', 'localtime') AND deadline <= date('now', '+3 days', 'localtime')" + order_query, (user_id,))
    else:
        # 全件（期限が設定されているものを上部にソート、その後は優先度順）
        c.execute(base_query + " ORDER BY CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END, deadline ASC, CASE priority WHEN '高' THEN 1 WHEN '中' THEN 2 WHEN '低' THEN 3 ELSE 4 END", (user_id,))
        
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

def get_all_active_todos():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, user_id, task, deadline, priority, reminded_24h, reminded_3h FROM todos WHERE deadline IS NOT NULL AND deadline != ''")
    rows = c.fetchall()
    conn.close()
    return rows

def update_todo_reminded(todo_id, column_name, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if column_name in ["reminded_24h", "reminded_3h"]:
        c.execute(f"UPDATE todos SET {column_name} = ? WHERE id = ?", (value, todo_id))
    conn.commit()
    conn.close()
