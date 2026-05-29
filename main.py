import discord
from discord import app_commands
from discord.ext import commands
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

database.init_db()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

channel_modes = {}
channel_histories = {}
long_term_memories = {}
DAILY_LIMIT = 20

def get_history_text(channel_id):
    long_term = long_term_memories.get(channel_id, "")
    history = channel_histories.get(channel_id, [])
    text = ""
    if long_term: 
        text += "【重要：長期記憶】\n" + long_term + "\n\n"
    if history:
        text += "【直近の会話履歴】\n"
        for h in history:
            role_name = "ユーザー" if h["role"] == "user" else "AI"
            text += role_name + ": " + h['text'] + "\n"
        text += "（履歴ここまで）\n\n"
    return text

def add_history(channel_id, role, text):
    if channel_id not in channel_histories: 
        channel_histories[channel_id] = []
    channel_histories[channel_id].append({"role": role, "text": text})

async def send_response(channel, text):
    if not text: return
    if len(text) > 1500:
        with io.BytesIO(text.encode('utf-8-sig')) as f:
            await channel.send(content="📄 回答が長文になったためファイル出力しました：", file=discord.File(f, filename="ai_secretary_report.txt"))
    else:
        await channel.send(text)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} 起動成功 - タブレット完全対応・安全RAG版")

@bot.tree.command(name="todo_add", description="📝 新しいTODOタスクを追加します")
async def slash_todo_add(interaction: discord.Interaction, task: str):
    database.add_todo(interaction.user.id, task)
    await interaction.response.send_message(embed=discord.Embed(title="📝 TODO 追加", description=f"タスク: **{task}**", color=0x3498db))

@bot.tree.command(name="todo_list", description="📋 現在のTODO一覧を確認します")
async def slash_todo_list(interaction: discord.Interaction):
    todos = database.get_todos(interaction.user.id)
    if not todos:
        await interaction.response.send_message(embed=discord.Embed(title="📋 TODO 一覧", description="現在登録されているタスクはありません。", color=0x95a5a6))
        return
    desc = ""
    for t in todos:
        desc += f"**ID:{t[0]}** - {t[1]}\n"
    await interaction.response.send_message(embed=discord.Embed(title="📋 TODO 一覧", description=desc, color=0xf1c40f))

@bot.tree.command(name="todo_done", description="✅ 完了したTODOを削除します")
async def slash_todo_done(interaction: discord.Interaction, todo_id: int):
    if database.delete_todo(todo_id, interaction.user.id):
        await interaction.response.send_message(embed=discord.Embed(title="✅ TODO 完了", description=f"ID:{todo_id} のタスクを完了しました！", color=0x2ecc71))
    else:
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description=f"ID:{todo_id} が見つかりません。", color=0xe74c3c))

@bot.tree.command(name="role", description="🎭 AIの性格（人格）を変更します")
async def slash_role(interaction: discord.Interaction, persona: str):
    if persona.lower() in ["リセット", "reset", "解除"]:
        database.delete_role(interaction.channel_id)
        await interaction.response.send_message(embed=discord.Embed(title="🔄 人格リセット", description="AIの人格を通常の「優秀な秘書」に戻しました。", color=0x9b59b6))
    else:
        database.set_role(interaction.channel_id, persona)
        await interaction.response.send_message(embed=discord.Embed(title="🎭 人格設定", description=f"AIの人格を **【{persona}】** に設定しました。", color=0xe67e22))

@bot.tree.command(name="knowledge_add", description="⚖️ AI専用の知識（論証集や六法など）を登録します")
async def slash_knowledge_add(interaction: discord.Interaction, keyword: str, content: str):
    database.add_knowledge(keyword, content)
    title_text = "⚖️ 知識データベース登録完了"
    desc_text = "キーワード **【" + keyword + "】** で知識を記憶しました。\n今後、質問にこの単語が含まれるとAIが自動参照します。\n\n"
    desc_text += "■ 登録内容の先頭部分\n" + content[:200] + "..."
    embed = discord.Embed(title=title_text, description=desc_text, color=0x1abc9c)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="memory", description="🧠 【管理者用】現在の長期記憶を確認します")
async def slash_memory(interaction: discord.Interaction):
    long_term = long_term_memories.get(interaction.channel_id, "まだ長期記憶はありません。")
    await interaction.response.send_message(embed=discord.Embed(title="🧠 AIの現在の脳内メモリ", description=long_term, color=0x1abc9c))

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

        todos = database.get_todos(message.author.id)
        if todos:
            todo_text = "【現在のTODO】\n"
            for t in todos:
                todo_text += f"・{t[1]}\n"
            history_str = todo_text + "\n" + history_str

        rag_context = database.search_knowledge(user_text)
        if rag_context:
            await message.channel.send(embed=discord.Embed(description="📚 登録された専門知識（RAG）を参照しています...", color=0x1abc9c))

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
            if message.attachments and any(message.attachments[0].filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                await message.channel.send("👀 画像を確認しています...")
                img_data = requests.get(message.attachments[0].url).content
                img = Image.open(io.BytesIO(img_data))
                instructions = f"あなたは{current_role}です。\n{history_str}\n{rag_context}\n{url_content}\n指示: {user_text if user_text else '説明して'}"
                response = client.models.generate_content(model=current_model, contents=[img, instructions])
                await send_response(message.channel, response.text)
                if user_text: add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                return

            if message.attachments and message.attachments[0].filename.lower().endswith('.pdf'):
                await message.channel.send("📄 PDFを解析しています...")
                pdf_data = requests.get(message.attachments[0].url).content
                pdf_text = ""
                with io.BytesIO(pdf_data) as pdf_file:
                    reader = pypdf.PdfReader(pdf_file)
                    for page in reader.pages:
                        if page.extract_text(): pdf_text += page.extract_text() + "\n"
                prompt = f"あなたは{current_role}です。\n{history_str}\n{rag_context}\n{url_content}\n【PDF】\n{pdf_text[:30000]}\n指示: {user_text if user_text else '要約して'}"
                response = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, response.text)
                if user_text: add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                return

            if not user_text and not url_content:
                await message.channel.send("はい、何でしょうか？")
                return

            status_msg = await message.channel.send(embed=discord.Embed(description="🤔 分析中...", color=0x34495e))
            intent_check = client.models.generate_content(model='gemini-2.5-flash', contents=f"以下の文章が検索必要な質問か判定し、必要ならYES、不要ならNOと答えてください。\n文章：{user_text}")
            await asyncio.sleep(3)
            
            if "YES" in intent_check.text.upper():
                await status_msg.edit(embed=discord.Embed(description="🔍 事実確認を行っています...", color=0x2980b9))
                url = "https://api.tavily.com/search"
                payload = {"api_key": TAVILY_API_KEY, "query": user_text, "search_depth": "advanced", "max_results": 5}
                search_data = requests.post(url, json=payload).json()
                context = "【ウェブ検索の情報】\n\n"
                if "results" in search_data:
                    for idx, res in enumerate(search_data["results"]):
                        context += f"URL: {res.get('url')}\n内容: {res.get('content')}\n\n"

                prompt = f"あなたは{current_role}です。特に【登録知識】がある場合は最優先して答えてください。\n\n{history_str}\n{rag_context}\n{context}\n質問: {user_text}"
                await status_msg.edit(embed=discord.Embed(description="🧠 情報統合中...", color=0x8e44ad))
                answer = client.models.generate_content(model=current_model, contents=prompt)
                await status_msg.delete()
                await message.channel.send(embed=discord.Embed(title=f"🤖 統合回答", description=answer.text, color=0xecf0f1))
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", answer.text)
                
            else:
                prompt = f"あなたは{current_role}です。特に【登録知識】がある場合はその内容をベースに答えてください。\n\n{history_str}\n{rag_context}\n質問：{user_text}"
                response = client.models.generate_content(model=current_model, contents=prompt)
                await status_msg.delete()
                await message.channel.send(embed=discord.Embed(title=f"🤖 {current_role}", description=response.text, color=0xecf0f1))
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)

        except Exception as e:
            if 'status_msg' in locals(): await status_msg.delete()
            await message.channel.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

bot.run(DISCORD_TOKEN)
