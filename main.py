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
from datetime import datetime, timedelta, timezone
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from pydantic import BaseModel, Field

database.init_db()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GCP_CRED_JSON = os.getenv("GCP_CRED_JSON")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

client = genai.Client(api_key=GEMINI_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

channel_modes = {}
DAILY_LIMIT = 20

safety_config = types.GenerateContentConfig(
    safety_settings=[
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    ]
)

class Milestone(BaseModel):
    task: str = Field(description="タスク内容")
    deadline: str = Field(description="YYYY-MM-DD")
    priority: str = Field(description="高/中/低")

class MilestoneList(BaseModel):
    milestones: list[Milestone]

def get_embedding(text):
    response = client.models.embed_content(model='text-embedding-004', contents=text)
    return response.embeddings[0].values

def get_history_text(channel_id):
    recent_messages = database.get_recent_messages(channel_id, limit=8)
    text = ""
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
            await channel.send(content="📄 回答が長文になったためファイル出力しました：", file=discord.File(f, filename="report.txt"))
    else:
        await channel.send(text)

@bot.event
async def on_ready():
    await bot.tree.sync()
    if not reminder_loop.is_running():
        reminder_loop.start()
    print(f"{bot.user} 起動完了 - 全機能＋リアルタイム時計搭載版")

@tasks.loop(hours=1)
async def reminder_loop():
    await bot.wait_until_ready()
    # 日本時間でループ処理を管理
    JST = timezone(timedelta(hours=+9), 'JST')
    now = datetime.now(JST).replace(tzinfo=None)
    
    if now.hour == 7:
        user_ids = database.get_all_users_with_todos()
        if user_ids:
            weather_context = ""
            try:
                url = "https://api.tavily.com/search"
                payload = {"api_key": TAVILY_API_KEY, "query": "岡山市 今日の天気 気温 傘が必要か", "search_depth": "basic", "max_results": 2}
                search_data = requests.post(url, json=payload).json()
                if "results" in search_data:
                    weather_context = "【本日の岡山市の気象事実情報】\n"
                    for r in search_data["results"]: weather_context += f"{r.get('content')}\n"
            except: weather_context = "【本日の気象事実情報】取得失敗\n"

            for u_id in user_ids:
                if not database.check_and_record_briefing(u_id): continue
                user = bot.get_user(u_id)
                if not user:
                    try: user = await bot.fetch_user(u_id)
                    except: continue
                
                today_todos = database.get_todos(u_id, filter_type="today")
                todo_str = "【本日のユーザーの締切TODO】\n"
                if not today_todos: todo_str += "・本日が締切のタスクはありません。\n"
                else:
                    for t in today_todos: todo_str += f"・[優先度:{t[3]}] {t[1]}\n"
                
                briefing_prompt = f"あなたは個人のAI秘書です。毎朝の定時報告を作成してください。\n{weather_context}\n{todo_str}"
                try:
                    response = client.models.generate_content(model='gemini-2.5-flash', contents=briefing_prompt, config=safety_config)
                    embed = discord.Embed(title="🌤️ モーニング・ブリーフィング", description=response.text, color=0x3498db)
                    await user.send(embed=embed)
                except: pass

    active_todos = database.get_all_active_todos()
    for todo in active_todos:
        todo_id, user_id, task, deadline_str, priority, reminded_24h, reminded_3h = todo
        try: deadline = datetime.strptime(f"{deadline_str} 23:59:59", "%Y-%m-%d %H:%M:%S")
        except ValueError: continue
        time_left = deadline - now
        hours_left = time_left.total_seconds() / 3600
        user = bot.get_user(user_id)
        if not user:
            try: user = await bot.fetch_user(user_id)
            except: continue
            
        if 0 < hours_left <= 24 and reminded_24h == 0:
            embed = discord.Embed(title="⚠️ TODO締切24時間前通知", description=f"タスク **「{task}」**", color=0xe67e22)
            try:
                await user.send(embed=embed)
                database.update_todo_reminded(todo_id, "reminded_24h", 1)
            except: pass
            
        if 0 < hours_left <= 3 and reminded_3h == 0:
            embed = discord.Embed(title="🚨 TODO締切直前通知 (残り3時間)", description=f"タスク **「{task}」**", color=0xe74c3c)
            try:
                await user.send(embed=embed)
                database.update_todo_reminded(todo_id, "reminded_3h", 1)
            except: pass

@bot.tree.command(name="calendar_add", description="📅 指定した予定をGoogle Calendarに自動登録します")
@app_commands.describe(title="予定のタイトル", date="日付 (YYYY-MM-DD)", time="開始時間 (HH:MM)", duration="所要時間(分)")
async def slash_calendar_add(interaction: discord.Interaction, title: str, date: str, time: str, duration: int = 60):
    await interaction.response.defer()
    if not GCP_CRED_JSON or not GOOGLE_CALENDAR_ID:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ 設定エラー", description="GCPキーかカレンダーIDが未設定です。", color=0xe74c3c))
        return
    try:
        cred_dict = json.loads(GCP_CRED_JSON)
        credentials = service_account.Credentials.from_service_account_info(cred_dict, scopes=['https://www.googleapis.com/auth/calendar'])
        service = build('calendar', 'v3', credentials=credentials)
        start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration)
        event = {
            'summary': title,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
        }
        service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        embed = discord.Embed(title="📅 Googleカレンダー登録完了", color=0x3498db)
        embed.add_field(name="予定", value=title, inline=False)
        embed.add_field(name="日時", value=f"{start_dt.strftime('%Y/%m/%d %H:%M')} 〜", inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ カレンダーAPIエラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

@bot.tree.command(name="weekly_score", description="📊 今週のタスク完了率やペースをAIが分析し、秘書スコアを出力します")
async def slash_weekly_score(interaction: discord.Interaction):
    await interaction.response.defer()
    user_id = interaction.user.id
    completed_count = database.get_weekly_completed_count(user_id)
    active_todos = database.get_todos(user_id, filter_type="all")
    overdue_count = 0
    now = datetime.now()
    todo_list_text = ""
    for t in active_todos:
        todo_list_text += f"・{t[1]} (期限: {t[2]})\n"
        if t[2]:
            try:
                deadline = datetime.strptime(f"{t[2]} 23:59:59", "%Y-%m-%d %H:%M:%S")
                if deadline < now: overdue_count += 1
            except: pass

    prompt = f"あなたは優秀なAI秘書です。以下の実績データに基づき、週次レポートを作成してください。\n【完了数】: {completed_count}件\n【残タスク】: {len(active_todos)}件\n【超過タスク】: {overdue_count}件\n【現在のTODO】\n{todo_list_text}"
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=safety_config)
        embed = discord.Embed(title="📊 AI秘書スコア・週次レポート", description=response.text, color=0xf1c40f)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

@bot.tree.command(name="debate", description="🎓 厳格な法学教授（AI）とソクラテス・メソッドによる反対尋問を開始します")
@app_commands.describe(topic="議論したいテーマ")
async def slash_debate(interaction: discord.Interaction, topic: str):
    await interaction.response.defer()
    channel_id = interaction.channel_id
    prof_persona = "法科大学院の厳格な教授。ソクラテス・メソッドを用います。すぐに正解を教えず、鋭い質問を投げて論理の穴を詰めさせます。"
    database.set_role(channel_id, prof_persona)
    prompt = f"あなたは{prof_persona}\nテーマ：「{topic}」について、最も鋭い質問を1つ投げかけて議論をスタートしてください。"
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=safety_config)
        embed = discord.Embed(title=f"🎓 ソクラテス・メソッド開始: {topic}", description=response.text, color=0xc0392b)
        await interaction.followup.send(embed=embed)
        database.add_message(channel_id, interaction.user.id, "user", f"【テーマ設定】{topic}")
        database.add_message(channel_id, bot.user.id, "model", response.text)
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

@bot.tree.command(name="plan_generate", description="🧠 大きな目標から逆算して、締切付きのTODOを一括登録します")
@app_commands.describe(goal="大きな目標", deadline="最終締切日 (YYYY-MM-DD)")
async def slash_plan_generate(interaction: discord.Interaction, goal: str, deadline: str):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", deadline):
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description="締切日は `YYYY-MM-DD` 形式で入力してください。", color=0xe74c3c), ephemeral=True)
        return
    await interaction.response.defer()
    today_str = datetime.now().strftime("%Y-%m-%d")
    milestone_config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=MilestoneList, safety_settings=safety_config.safety_settings)
    prompt = f"目標「{goal}」を最終締切「{deadline}」までに達成するため、今日「{today_str}」からのマイルストーンを3〜5個生成してください。"
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=milestone_config)
        milestones_data = MilestoneList.model_validate_json(response.text)
        desc_text = f"目標 **「{goal}」** のマイルストーンをTODOに登録しました！\n\n"
        for m in milestones_data.milestones:
            database.add_todo(interaction.user.id, f"[{goal[:10]}...] {m.task}", m.deadline, m.priority)
            p_emoji = "🔴" if m.priority == "高" else "🟡" if m.priority == "中" else "🔵"
            desc_text += f"{p_emoji} **{m.task}**\n   📅 期限: {m.deadline}\n\n"
        await interaction.followup.send(embed=discord.Embed(title="🧠 マイルストーン一括登録完了", description=desc_text, color=0x9b59b6))
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

@bot.tree.command(name="study_plan", description="🧠 現在のTODOリストから今日の学習計画を提案します")
async def slash_study_plan(interaction: discord.Interaction):
    await interaction.response.defer()
    todos = database.get_todos(interaction.user.id, filter_type="all")
    todo_context = "【現在のTODO】\n" + "".join([f"・{t[1]} (期限: {t[2]})\n" for t in todos]) if todos else "なし"
    history_str = get_history_text(interaction.channel_id)
    prompt = f"あなたはAI学習コーチです。以下のTODOと文脈から今日やるべき学習計画を3つ提案してください。\n{todo_context}\n{history_str}"
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=safety_config)
        await interaction.followup.send(embed=discord.Embed(title="🧠 本日の学習プラン", description=response.text, color=0x9b59b6))
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=str(e)[:500], color=0xe74c3c))

@bot.tree.command(name="todo_add", description="📝 期限付きTODOを追加します")
async def slash_todo_add(interaction: discord.Interaction, task: str, deadline: str = None, priority: str = "中"):
    database.add_todo(interaction.user.id, task, deadline, priority)
    await interaction.response.send_message(embed=discord.Embed(title="📝 TODO 追加完了", description=task, color=0x3498db))

@bot.tree.command(name="todo_list", description="📋 現在のTODO一覧を確認します")
async def slash_todo_list(interaction: discord.Interaction):
    todos = database.get_todos(interaction.user.id, filter_type="all")
    desc = "".join([f"**ID:{t[0]}** | [{t[3]}] **{t[1]}** (📅 {t[2]})\n" for t in todos]) if todos else "タスクはありません。"
    await interaction.response.send_message(embed=discord.Embed(title="📋 TODO 一覧", description=desc, color=0xf1c40f))

@bot.tree.command(name="todo_done", description="✅ 完了したTODOを履歴に記録して消去します")
async def slash_todo_done(interaction: discord.Interaction, todo_id: int):
    if database.complete_todo(todo_id, interaction.user.id):
        await interaction.response.send_message(embed=discord.Embed(title="✅ TODO 完了", description=f"ID:{todo_id} を完了し、実績スコアに加算しました！", color=0x2ecc71))
    else:
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description="該当のIDが見つかりません。", color=0xe74c3c))

@bot.tree.command(name="search_memory", description="🔍 過去のAIとの会話履歴を検索します")
async def slash_search_memory(interaction: discord.Interaction, keyword: str):
    await interaction.response.defer()
    results = database.search_messages_by_keyword(interaction.user.id, keyword, limit=5)
    desc = "".join([f"`{r[0]}` {'👤' if r[1]=='user' else '🤖'} {r[2][:80]}...\n\n" for r in results]) if results else "見つかりませんでした。"
    await interaction.followup.send(embed=discord.Embed(title=f"🔍 「{keyword}」の検索結果", description=desc, color=0x3498db))

@bot.tree.command(name="knowledge_add", description="⚖️ 意味検索（ベクトル）対応の専門知識を登録します")
async def slash_knowledge_add(interaction: discord.Interaction, keyword: str, content: str):
    await interaction.response.defer()
    try:
        vector = get_embedding(f"見出し:{keyword} 内容:{content}")
        database.add_knowledge_with_vector(keyword, content, vector)
        await interaction.followup.send(embed=discord.Embed(title="⚖️ 知識登録完了 (ベクトル化済)", description=f"【{keyword}】", color=0x1abc9c))
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=str(e)[:500], color=0xe74c3c))

@bot.tree.command(name="role", description="🎭 AIの性格を変更します")
async def slash_role(interaction: discord.Interaction, persona: str):
    if persona.lower() in ["リセット", "reset", "解除"]:
        database.delete_role(interaction.channel_id)
        await interaction.response.send_message(embed=discord.Embed(title="🔄 人格リセット", description="通常の「優秀な秘書」に戻しました。", color=0x9b59b6))
    else:
        database.set_role(interaction.channel_id, persona)
        await interaction.response.send_message(embed=discord.Embed(title="🎭 人格設定", description=f"AIを **【{persona}】** に設定しました。", color=0xe67e22))

@bot.event
async def on_message(message):
    if message.author.bot: return

    if bot.user.mentioned_in(message):
        is_allowed, current_count = database.check_and_increment_usage(message.author.id, limit=DAILY_LIMIT)
        if not is_allowed:
            await message.channel.send(embed=discord.Embed(title="🛑 利用上限", description="本日の上限に達しました。", color=0xe74c3c))
            return

        user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
        current_model = channel_modes.get(message.channel.id, 'gemini-2.5-flash')
        db_role = database.get_role(message.channel.id)
        current_role = db_role if db_role else "優秀なAI秘書"
        history_str = get_history_text(message.channel.id)

        rag_context = ""
        try:
            query_vector = get_embedding(user_text)
            rag_context = database.search_knowledge_by_vector(query_vector, top_k=2, threshold=0.65)
        except: pass

        if rag_context:
            await message.channel.send(embed=discord.Embed(description="📚 独自の専門知識（ベクトル検索）を参照中...", color=0x1abc9c))

        try:
            status_msg = await message.channel.send(embed=discord.Embed(description="🤔 分析中...", color=0x34495e))
            
            # --- ✅ リアルタイムの日本時間を取得 ---
            JST = timezone(timedelta(hours=+9), 'JST')
            now_time = datetime.now(JST).strftime("%Y年%m月%d日 %H時%M分")
            
            prompt = f"あなたは{current_role}です。【現在のリアルタイム日時は {now_time} です。時間に関する質問にはこれを基準にしてください】特に【関連知識】がある場合は最優先して答えてください。\n\n{history_str}\n{rag_context}\n質問: {user_text}"
            # ------------------------------------

            answer = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
            
            await status_msg.delete()
            await message.channel.send(embed=discord.Embed(title=f"🤖 {current_role}", description=answer.text, color=0xecf0f1))
            
            database.add_message(message.channel.id, message.author.id, "user", user_text)
            database.add_message(message.channel.id, message.author.id, "model", answer.text)
                
        except Exception as e:
            if 'status_msg' in locals(): await status_msg.delete()
            await message.channel.send(embed=
