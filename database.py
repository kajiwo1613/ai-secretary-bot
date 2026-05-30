import sqlite3
from datetime import datetime
import json
import math

DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 既存テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT, deadline TEXT, priority TEXT, reminded_24h INTEGER DEFAULT 0, reminded_3h INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS roles (channel_id INTEGER PRIMARY KEY, role_text TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage_logs (user_id INTEGER, date TEXT, count INTEGER, PRIMARY KEY(user_id, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS briefing_logs (user_id INTEGER, date TEXT, PRIMARY KEY(user_id, date))''')
    
    # 🆕 ① 会話履歴の永続化テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    user_id INTEGER,
                    role TEXT,
                    content TEXT,
                    created_at TEXT
                 )''')
                 
    # 🆕 ③ ベクトルRAG用の知識テーブル（embeddingカラムを追加）
    c.execute('''CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    keyword TEXT UNIQUE, 
                    content TEXT,
                    embedding TEXT
                 )''')
                 
    try:
        c.execute("ALTER TABLE knowledge ADD COLUMN embedding TEXT")
    except sqlite3.OperationalError: pass

    conn.commit()
    conn.close()

# --- ① & ② 会話履歴管理と検索 ---
def add_message(channel_id, user_id, role, content):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO messages (channel_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)", 
              (channel_id, user_id, role, content, now))
    conn.commit()
    conn.close()

def get_recent_messages(channel_id, limit=10):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?", (channel_id, limit))
    rows = c.fetchall()
    conn.close()
    return reversed(rows) # 古い順に戻す

def search_messages_by_keyword(user_id, keyword, limit=5):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 部分一致で過去の会話を検索
    search_term = f"%{keyword}%"
    c.execute("SELECT created_at, role, content FROM messages WHERE user_id = ? AND content LIKE ? ORDER BY id DESC LIMIT ?", 
              (user_id, search_term, limit))
    rows = c.fetchall()
    conn.close()
    return rows

# --- ③ 本格的ベクトルRAG機能 ---
def add_knowledge_with_vector(keyword, content, embedding_vector):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # ベクトル（配列）をJSON文字列にして保存
    vector_json = json.dumps(embedding_vector)
    c.execute("INSERT OR REPLACE INTO knowledge (keyword, content, embedding) VALUES (?, ?, ?)", 
              (keyword, content, vector_json))
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
            
    # スコア（類似度）が高い順にソート
    results.sort(key=lambda x: x["score"], reverse=True)
    
    matched_context = ""
    for res in results[:top_k]:
        matched_context += f"【関連知識: {res['keyword']} (類似度: {res['score']:.2f})】\n{res['content']}\n\n"
    return matched_context

# (以下、既存のTODO・Role管理関数はそのまま残してください)
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
        if row[0] >= limit: return False, row[0]
        c.execute("UPDATE usage_logs SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
        return True, row[0] + 1
    c.execute("INSERT INTO usage_logs (user_id, date, count) VALUES
