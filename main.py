import discord
from discord import app_commands
from discord.ext import commands, tasks
from google import genai
from google.genai import types
import os
import requests
from PIL import Image
import io
import pypdf
import asyncio
import re
from bs4 import BeautifulSoup
import database
from datetime import datetime

database.init_db()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

channel_modes = {}
long_term_memories = {}
DAILY_LIMIT = 20

safety_config = types.GenerateContentConfig(
    safety_settings=[
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    ]
)

# --- 共通ユーティリティ ---
def get_history_text(channel_id):
    long_term = long_term_memories.get(channel_id, "")
    # メモリではなく、SQLiteデータベースから履歴を取得
    recent_messages = database.get_recent_messages(channel_id, limit=8)
    text = ""
    if long_term: 
        text += "【重要：長期記憶】\n" + long_term + "\n\n"
    if recent_messages:
        text += "【直近の会話履歴】\n"
        for role, content in recent_messages:
            role_name = "ユーザー" if role == "user" else "AI"
            text += f"{role_name}: {content}\n"
        text += "（履歴ここまで）\n\n"
    return text

async def send_response(channel, text):
    if not text: return
    if len(text) > 1500:
        with io.BytesIO(text.encode('utf-8-sig')) as f:
            await channel.send(content="📄 回答が長文になったためファイル出力しました：", file=discord.File(f, filename="ai_secretary_report.txt"))
    else:
        await channel.send(text)

def get_embedding(text):
    """テキストをベクトル化する（Gemini 2.5の最新Embedding利用）"""
    response = client.models.embed_content(
        model='text-embedding-004', 
        contents=text
    )
    return response.embeddings[0].values

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} 起動成功 - ベクトルRAG・会話DB永続化版")

# 🆕 ② 過去の会話を検索するコマンド
@bot.tree.command(name="search_memory", description="🔍 過去のAIとの会話履歴からキーワードで検索します")
@app_commands.describe(keyword="検索したい単語（例: 共犯共同正犯、関西大学の過去問など）")
async def slash_search_memory(interaction: discord.Interaction, keyword: str):
    await interaction.response.defer()
    results = database.search_messages_by_keyword(interaction.user.id, keyword, limit=5)
    
    if not results:
        await interaction.followup.send(embed=discord.Embed(title="🔍 検索結果", description=f"「{keyword}」に関する会話履歴は見つかりませんでした。", color=0x95a5a6))
        return
        
    desc = ""
    for created_at, role, content in results:
        role_icon = "👤" if role == "user" else "🤖"
        # 長すぎる場合は切り詰める
        short_content = content[:80] + "..." if len(content) > 80 else content
        desc += f"`{created_at}` {role_icon} {short_content}\n\n"
        
    embed = discord.Embed(title=f"🔍 「{keyword}」の会話履歴検索", description=desc, color=0x3498db)
    await interaction.followup.send(embed=embed)

# 🆕 ③ 高度なRAG：ベクトル知識登録コマンド
@bot.tree.command(name="knowledge_add", description="⚖️ 意味検索（ベクトル）対応の専門知識を登録します")
@app_commands.describe(keyword="見出し", content="論証や条文の内容")
async def slash_knowledge_add(interaction: discord.Interaction, keyword: str, content: str):
    await interaction.response.defer()
    try:
        # 見出しと内容を結合して意味のベクトルを抽出
        text_to_embed = f"見出し:{keyword} 内容:{content}"
        vector = get_embedding(text_to_embed)
        
        database.add_knowledge_with_vector(keyword, content, vector)
        embed = discord.Embed(
            title="⚖️ 知識データベース登録完了 (ベクトル化済)", 
            description=f"キーワード **【{keyword}】** で意味情報を解析し記憶しました。\n今後、言葉が揺れてもAIが文脈から自動で引っ張り出します。", 
            color=0x1abc9c
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"ベクトル化に失敗しました: `{str(e)[:500]}`", color=0xe74c3c))

# 🆕 ④ Google Calendar連携の基盤コマンド
@bot.tree.command(name="calendar_add", description="📅 【準備中】予定をGoogle Calendarに同期します")
@app_commands.describe(title="予定のタイトル", date="日付 (YYYY-MM-DD)", time="開始時間 (例: 13:00)")
async def slash_calendar_add(interaction: discord.Interaction, title: str, date: str, time: str):
    """
    ⚠️ 開発者向けノート: 
    Google Calendar APIを使用するには、GCP(Google Cloud Platform)にて
    1. Calendar APIを有効化
    2. サービスアカウントを作成し、JSONキーをダウンロード
    3. そのJSONを環境変数またはファイルとしてRenderにアップロード
    する必要があります。このコマンドはUIとして先に用意しています。
    """
    embed = discord.Embed(
        title="📅 Google Calendar 連携準備中", 
        description=f"予定「{title}」({date} {time}) を受け付けました。\n\n※この機能を完全に有効化するには、GCPのサービスアカウント認証(credentials.json)の導入が必要です。現在はモックとして動作しています。", 
        color=0xf1c40f
    )
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot: return

    if bot.user.mentioned_in(message):
        is_allowed, current_count = database.check_and_increment_usage(message.author.id, limit=DAILY_LIMIT)
        if not is_allowed:
            await message.channel.send(embed=discord.Embed(title="🛑 本日の利用上限", description=f"1日{DAILY_LIMIT}回の上限に達しました。", color=0xe74c3c))
            return

        user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
        current_model = channel_modes.get(message.channel.id, 'gemini-2.5-flash')
        db_role = database.get_role(message.channel.id)
        current_role = db_role if db_role else "優秀なAI秘書"
        history_str = get_history_text(message.channel.id)

        # 🆕 ③ メッセージ送信時にクエリをベクトル化し、関連知識を抽出（意味検索）
        rag_context = ""
        try:
            query_vector = get_embedding(user_text)
            rag_context = database.search_knowledge_by_vector(query_vector, top_k=2, threshold=0.65)
        except: pass

        if rag_context:
            await message.channel.send(embed=discord.Embed(description="📚 独自の専門知識（ベクトル検索）を参照しています...", color=0x1abc9c))

        url_pattern = re.compile(r'https?://\S+')
        urls = url_pattern.findall(user_text)
        url_content = ""
        if urls:
            await message.channel.send("🌐 ウェブサイトを読み込んでいます...")
            for url in urls[:2]:
                try:
                    headers = {'User-Agent': 'Mozilla/5.0'}
                    res = requests.get(url, headers=headers, timeout=5)
                    soup = BeautifulSoup(res.text, 'html.parser')
                    url_content += f"【URL: {url} の内容】\n{soup.get_text(separator='\n', strip=True)[:3000]}\n\n"
                except: pass

        try:
            status_msg = await message.channel.send(embed=discord.Embed(description="🤔 分析中...", color=0x34495e))
            intent_check = client.models.generate_content(model='gemini-2.5-flash', contents=f"以下の文章が最新の事実検索必要な質問か判定し、必要ならYES、不要ならNOと答えてください。\n文章：{user_text}", config=safety_config)
            await asyncio.sleep(2)
            
            context = ""
            if "YES" in intent_check.text.upper():
                await status_msg.edit(embed=discord.Embed(description="🔍 事実確認を行っています...", color=0x2980b9))
                url = "https://api.tavily.com/search"
                payload = {"api_key": TAVILY_API_KEY, "query": user_text, "search_depth": "advanced", "max_results": 5}
                search_data = requests.post(url, json=payload).json()
                context = "【ウェブ検索の情報】\n\n"
                if "results" in search_data:
                    for idx, res in enumerate(search_data["results"]):
                        context += f"URL: {res.get('url')}\n内容: {res.get('content')}\n\n"

            prompt = f"あなたは{current_role}です。特に【関連知識】がある場合は最優先して答えてください。\n\n{history_str}\n{rag_context}\n{context}\n質問: {user_text}"
            await status_msg.edit(embed=discord.Embed(description="🧠 情報統合中...", color=0x8e44ad))
            
            answer = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
            await status_msg.delete()
            await message.channel.send(embed=discord.Embed(title=f"🤖 {current_role}", description=answer.text, color=0xecf0f1))
            
            # 🆕 会話履歴をデータベースへ永続保存
            database.add_message(message.channel.id, message.author.id, "user", user_text)
            database.add_message(message.channel.id, message.author.id, "model", answer.text)
                
        except Exception as e:
            if 'status_msg' in locals(): await status_msg.delete()
            await message.channel.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

bot.run(DISCORD_TOKEN)
