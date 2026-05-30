import sqlite3
from datetime import datetime
import json
import math

DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT, deadline TEXT, priority TEXT, reminded_24h INTEGER DEFAULT 0, reminded_3h INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS roles (channel_id INTEGER PRIMARY KEY, role_text TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage_logs (user_id INTEGER, date TEXT, count INTEGER, PRIMARY KEY(user_id, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS briefing_logs (user_id INTEGER, date TEXT, PRIMARY KEY(user_id, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER, user_id INTEGER, role TEXT, content TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT UNIQUE, content TEXT, embedding TEXT)''')
    
    # 🆕 AI秘書スコア計算用の「完了済みTODO履歴」テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS completed_todos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT, completed_at TEXT)''')

    try:
        c.execute("ALTER TABLE knowledge ADD COLUMN embedding TEXT")
    except sqlite3.OperationalError: pass
    conn.commit()
    conn.close()

# --- 会話履歴・RAG機能（前回と同じ） ---
def add_message(channel_id, user_id, role, content):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO messages (channel_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)", (channel_id, user_id, role, content, now))
    conn.commit()
    conn.close()

def get_recent_messages(channel_id, limit=10):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?", (channel_id, limit))
    rows = c.fetchall()
    conn.close()
    return reversed(rows)

def search_messages_by_keyword(user_id, keyword, limit=5):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    search_term = f"%{keyword}%"
    c.execute("SELECT created_at, role, content FROM messages WHERE user_id = ? AND content LIKE ? ORDER BY id DESC LIMIT ?", (user_id, search_term, limit))
    rows = c.fetchall()
    conn.close()
    return rows

def add_knowledge_with_vector(keyword, content, embedding_vector):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    vector_json = json.dumps(embedding_vector)
    c.execute("INSERT OR REPLACE INTO knowledge (keyword, content, embedding) VALUES (?, ?, ?)", (keyword, content, vector_json))
    conn.commit()
    conn.close()

def cosine_similarity(v1, v2):
    dot_product = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0: return 0
    return dot_product / (mag1 * mag2)

def search_knowledge_by_vector(query_vector, top_k=2, threshold=0.75):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT keyword, content, embedding FROM knowledge WHERE embedding IS NOT NULL")
    rows = c.fetchall()
    conn.close()
    results = []
    for row in rows:
        keyword, content, emb_str = row
        db_vector = json.loads(emb_str)
        similarity = cosine_similarity(query_vector, db_vector)
        if similarity >= threshold:
            results.append({"keyword": keyword, "content": content, "score": similarity})
    results.sort(key=lambda x: x["score"], reverse=True)
    matched_context = ""
    for res in results[:top_k]:
        matched_context += f"【関連知識: {res['keyword']} (類似度: {res['score']:.2f})】\n{res['content']}\n\n"
    return matched_context

# --- TODO管理機能（完了処理を拡張） ---
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
    if filter_type == "today":
        c.execute(base_query + " AND deadline = ? ORDER BY CASE priority WHEN '高' THEN 1 WHEN '中' THEN 2 WHEN '低' THEN 3 ELSE 4 END, id ASC", (user_id, today_str))
    else:
        c.execute(base_query + " ORDER BY CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END, deadline ASC, CASE priority WHEN '高' THEN 1 WHEN '中' THEN 2 WHEN '低' THEN 3 ELSE 4 END", (user_id,))
    todos = c.fetchall()
    conn.close()
    return todos

# 🆕 単に削除するのではなく、完了テーブルに移動させてスコア計算に使う
def complete_todo(todo_id, user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT task FROM todos WHERE id = ? AND user_id = ?", (todo_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO completed_todos (user_id, task, completed_at) VALUES (?, ?, ?)", (user_id, row[0], now))
    c.execute("DELETE FROM todos WHERE id = ? AND user_id = ?", (todo_id, user_id))
    conn.commit()
    conn.close()
    return True

# 🆕 過去7日間の完了タスク数を取得
def get_weekly_completed_count(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM completed_todos WHERE user_id = ? AND completed_at >= date('now', '-7 days', 'localtime')", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

# --- その他既存の関数 ---
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
        if row[0] >= limit: return False, row[0]
        c.execute("UPDATE usage_logs SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
        return True, row[0] + 1
    c.execute("INSERT INTO usage_logs (user_id, date, count) VALUES (?, ?, 1)", (user_id, today))
    conn.commit()
    conn.close()
    return True, 1
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
    c.execute(f"UPDATE todos SET {column_name} = ? WHERE id = ?", (value, todo_id))
    conn.commit()
    conn.close()
def get_all_users_with_todos():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT DISTINCT user_id FROM todos")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]
def check_and_record_briefing(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM briefing_logs WHERE user_id = ? AND date = ?", (user_id, today))
    row = c.fetchone()
    if row:
        conn.close()
        return False
    c.execute("INSERT INTO briefing_logs (user_id, date) VALUES (?, ?)", (user_id, today))
    conn.commit()
    conn.close()
    return True
