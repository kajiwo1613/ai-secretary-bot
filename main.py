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
channel_histories = {}
long_term_memories = {}
DAILY_LIMIT = 20

# ⚖️ 法的議論（殺人、傷害、わいせつ等）の誤ブロックを完全に防ぐセーフティ無効化設定
safety_config = types.GenerateContentConfig(
    safety_settings=[
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    ]
)

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
            await channel.send(content="📄 回宿が長文になったためファイル出力しました：", file=discord.File(f, filename="ai_secretary_report.txt"))
    else:
        await channel.send(text)

@bot.event
async def on_ready():
    await bot.tree.sync()
    if not reminder_loop.is_running():
        reminder_loop.start()
    print(f"{bot.user} 起動成功 - タブレット完全対応・リマインダー・安全RAG版")

# ⏰ ② 自動リマインダーシステム（1時間ごとにバックグラウンドでDBを自動チェック）
@tasks.loop(hours=1)
async def reminder_loop():
    await bot.wait_until_ready()
    active_todos = database.get_all_active_todos()
    now = datetime.now()
    
    for todo in active_todos:
        todo_id, user_id, task, deadline_str, priority, reminded_24h, reminded_3h = todo
        try:
            # 締切日の 23:59:59 を最終デッドラインとして残り時間を計算
            deadline = datetime.strptime(f"{deadline_str} 23:59:59", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
            
        time_left = deadline - now
        hours_left = time_left.total_seconds() / 3600
        
        user = bot.get_user(user_id)
        if not user:
            try: user = await bot.fetch_user(user_id)
            except: continue
            
        # 24時間前リマインド (残り24時間以内、かつ未通知)
        if 0 < hours_left <= 24 and reminded_24h == 0:
            p_emoji = "🔴" if priority == "高" else "🟡" if priority == "中" else "🔵"
            embed = discord.Embed(
                title="⚠️ 【AI秘書】TODO締切24時間前通知",
                description=f"タスク **「{task}」** の締切まで残り24時間を切りました！",
                color=0xe67e22
            )
            embed.add_field(name="期限日", value=deadline_str, inline=True)
            embed.add_field(name="優先度", value=f"{p_emoji} {priority}", inline=True)
            try:
                await user.send(embed=embed)
                database.update_todo_reminded(todo_id, "reminded_24h", 1)
            except discord.Forbidden: pass
            
        # 3時間前リマインド (残り3時間以内、かつ未通知)
        if 0 < hours_left <= 3 and reminded_3h == 0:
            p_emoji = "🔴" if priority == "高" else "🟡" if priority == "中" else "🔵"
            embed = discord.Embed(
                title="🚨 【AI秘書】TODO締切直前通知 (残り3時間)",
                description=f"タスク **「{task}」** の締切まで残り3時間を切っています！お急ぎください！",
                color=0xe74c3c
            )
            embed.add_field(name="期限日", value=deadline_str, inline=True)
            embed.add_field(name="優先度", value=f"{p_emoji} {priority}", inline=True)
            try:
                await user.send(embed=embed)
                database.update_todo_reminded(todo_id, "reminded_3h", 1)
            except discord.Forbidden: pass

# 📝 ① 締切付きTODO追加（Slash Command）
@bot.tree.command(name="todo_add", description="📝 期限と優先度を指定してTODOタスクを追加します")
@app_commands.describe(task="タスク内容", deadline="期限 (例: 2026-06-10)", priority="優先度 (高・中・低)")
@app_commands.choices(priority=[
    app_commands.Choice(name="🔴 高", value="高"),
    app_commands.Choice(name="🟡 中", value="中"),
    app_commands.Choice(name="🔵 低", value="低")
])
async def slash_todo_add(interaction: discord.Interaction, task: str, deadline: str = None, priority: str = "中"):
    if deadline and not re.match(r"^\d{4}-\d{2}-\d{2}$", deadline):
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description="期限は `YYYY-MM-DD` 形式（例: 2026-06-10）で入力してください。", color=0xe74c3c), ephemeral=True)
        return
        
    database.add_todo(interaction.user.id, task, deadline, priority)
    
    embed = discord.Embed(title="📝 TODO 追加完了", color=0x3498db)
    embed.add_field(name="タスク", value=task, inline=False)
    embed.add_field(name="期限", value=deadline if deadline else "未設定", inline=True)
    embed.add_field(name="優先度", value=priority, inline=True)
    await interaction.response.send_message(embed=embed)

# 📋 ① 優先度・期限順TODO表示（Slash Command）
@bot.tree.command(name="todo_list", description="📋 現在のTODO一覧を確認します（条件絞り込み可能）")
@app_commands.choices(filter=[
    app_commands.Choice(name="全てのタスク", value="all"),
    app_commands.Choice(name="今日が締切のタスク", value="today"),
    app_commands.Choice(name="3日以内に締切のタスク", value="3days")
])
async def slash_todo_list(interaction: discord.Interaction, filter: str = "all"):
    todos = database.get_todos(interaction.user.id, filter_type=filter)
    title_map = {"all": "📋 TODO 一覧 (全件)", "today": "⏰ TODO 一覧 (本日締切)", "3days": "⏳ TODO 一覧 (3日以内締切)"}
    
    if not todos:
        await interaction.response.send_message(embed=discord.Embed(title=title_map[filter], description="該当するTODOタスクはありません。", color=0x95a5a6))
        return
        
    desc = ""
    for t in todos:
        todo_id, task, deadline, priority = t
        p_emoji = "🔴" if priority == "高" else "🟡" if priority == "中" else "🔵"
        dl_text = f" (📅 期限: {deadline})" if deadline else " (📅 期限なし)"
        desc += f"**ID:{todo_id}** | {p_emoji} [{priority}] **{task}**{dl_text}\n"
        
    await interaction.response.send_message(embed=discord.Embed(title=title_map[filter], description=desc, color=0xf1c40f))

@bot.tree.command(name="todo_done", description="✅ 完了したTODOを削除します")
async def slash_todo_done(interaction: discord.Interaction, todo_id: int):
    if database.delete_todo(todo_id, interaction.user.id):
        await interaction.response.send_message(embed=discord.Embed(title="✅ TODO 完了", description=f"ID:{todo_id} のタスクを完了しました！", color=0x2ecc71))
    else:
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description=f"ID:{todo_id} が見つかりません。", color=0xe74c3c))

# 🧠 ③ AI学習コーチ機能（Slash Command）
@bot.tree.command(name="study_plan", description="🧠 あなたのTODOと直近の状況から、今日の最適な学習計画をAIが提案します")
async def slash_study_plan(interaction: discord.Interaction):
    await interaction.response.defer()
    user_id = interaction.user.id
    channel_id = interaction.channel_id
    
    todos = database.get_todos(user_id, filter_type="all")
    todo_context = "【ユーザーの現在のTODOリスト】\n"
    if not todos:
        todo_context += "・現在登録されているTODOはありません。\n"
    else:
        for t in todos:
            _, task, deadline, priority = t
            dl = deadline if deadline else "期限なし"
            todo_context += f"・タスク名: {task} (期限: {dl}, 優先度: {priority})\n"
            
    history_str = get_history_text(channel_id)
    current_model = channel_modes.get(channel_id, 'gemini-2.5-flash')
    
    prompt = f"""
あなたは法学部生・資格受験生を強力に支える、優秀なAI学習コーチ兼秘書です。
ユーザーのTODOリスト、締め切り、およびこれまでの会話の文脈を分析し、今日最も重点的に取り組むべき『具体的・現実的、かつ心理的負担を抑えた本日の学習計画』を組み立ててください。

{todo_context}

{history_str}

【出力のガイドライン】
1. モチベーションを維持するため、今日やるべきことを優先順位をつけて最大3つまでに絞り込んで提案してください。
2. それぞれのタスクについて「なぜ今日やるべきなのか（締切や優先度に基づく理由）」と、「推奨する勉強時間（例: 30分、2時間など）」を具体的に提示してください。
3. 受験生に寄り添い、理知的かつ励ましを含んだトーンで回答してください。
"""

    try:
        response = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
        embed = discord.Embed(title="🧠 AI学習コーチによる本日のプラン", description=response.text, color=0x9b59b6)
        embed.set_footer(text="タスクが完了したら /todo_done で整理しましょう。")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

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

        todos = database.get_todos(message.author.id, filter_type="all")
        if todos:
            todo_text = "【現在のTODO】\n"
            for t in todos:
                p_emoji = "🔴" if t[3] == "高" else "🟡" if t[3] == "中" else "🔵"
                dl = f"(期限: {t[2]})" if t[2] else ""
                todo_text += f"・{p_emoji} {t[1]} {dl}\n"
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
                response = client.models.generate_content(model=current_model, contents=[img, instructions], config=safety_config)
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
                response = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
                await send_response(message.channel, response.text)
                if user_text: add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                return

            if not user_text and not url_content:
                await message.channel.send("はい、何でしょうか？")
                return

            status_msg = await message.channel.send(embed=discord.Embed(description="🤔 分析中...", color=0x34495e))
            intent_check = client.models.generate_content(model='gemini-2.5-flash', contents=f"以下の文章が検索必要な質問か判定し、必要ならYES、不要ならNOと答えてください。\n文章：{user_text}", config=safety_config)
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
                answer = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
                await status_msg.delete()
                await message.channel.send(embed=discord.Embed(title=f"🤖 統合回答", description=answer.text, color=0xecf0f1))
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", answer.text)
                
            else:
                prompt = f"あなたは{current_role}です。特に【登録知識】がある場合はその内容をベースに答えてください。\n\n{history_str}\n{rag_context}\n質問：{user_text}"
                response = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
                await status_msg.delete()
                await message.channel.send(embed=discord.Embed(title=f"🤖 {current_role}", description=response.text, color=0xecf0f1))
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)

        except Exception as e:
            if 'status_msg' in locals(): await status_msg.delete()
            await message.channel.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

bot.run(DISCORD_TOKEN)
