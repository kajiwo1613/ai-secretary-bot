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
from datetime import datetime, timedelta
import json

# --- Google Calendar API用ライブラリ ---
from google.oauth2 import service_account
from googleapiclient.discovery import build

database.init_db()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# 🆕 GCPのサービスアカウント情報（JSON文字列として環境変数に保存）
GCP_CRED_JSON = os.getenv("GCP_CRED_JSON")
# カレンダー連携を行うユーザーのGmailアドレス（これも環境変数に登録）
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
            await channel.send(content="📄 回答がファイル出力されました：", file=discord.File(f, filename="report.txt"))
    else:
        await channel.send(text)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} 起動完了 - カレンダー連携・AIスコア・ソクラテスメソッド搭載版")

# -------------------------------------------------------------------
# 🔥 新機能1: Google Calendar連携コマンド
# -------------------------------------------------------------------
@bot.tree.command(name="calendar_add", description="📅 指定した予定をGoogle Calendarに自動登録します")
@app_commands.describe(title="予定のタイトル", date="日付 (YYYY-MM-DD)", time="開始時間 (HH:MM)", duration="所要時間(分)")
async def slash_calendar_add(interaction: discord.Interaction, title: str, date: str, time: str, duration: int = 60):
    await interaction.response.defer()
    
    if not GCP_CRED_JSON or not GOOGLE_CALENDAR_ID:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ 設定エラー", description="GCPのJSONキー、またはカレンダーIDがRenderの環境変数に設定されていません。", color=0xe74c3c))
        return
        
    try:
        # 文字列として保存したJSONキーを辞書型に変換して認証
        cred_dict = json.loads(GCP_CRED_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            cred_dict, scopes=['https://www.googleapis.com/auth/calendar']
        )
        service = build('calendar', 'v3', credentials=credentials)
        
        # 開始時間と終了時間の計算
        start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration)
        
        event = {
            'summary': title,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
        }
        
        # GoogleカレンダーAPIへ送信
        event = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        
        embed = discord.Embed(title="📅 Googleカレンダー登録完了", color=0x3498db)
        embed.add_field(name="予定", value=title, inline=False)
        embed.add_field(name="日時", value=f"{start_dt.strftime('%Y/%m/%d %H:%M')} 〜", inline=True)
        embed.set_footer(text="スマホのカレンダーアプリに同期されました。")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ カレンダーAPIエラー", description=f"`{str(e)[:500]}`\n※カレンダーの共有設定が正しく行われているか確認してください。", color=0xe74c3c))

# -------------------------------------------------------------------
# 🔥 新機能2: AI秘書スコア・週次レポート
# -------------------------------------------------------------------
@bot.tree.command(name="weekly_score", description="📊 今週のタスク完了率やペースをAIが分析し、秘書スコアを出力します")
async def slash_weekly_score(interaction: discord.Interaction):
    await interaction.response.defer()
    user_id = interaction.user.id
    
    # DBから実績を集計
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
                if deadline < now:
                    overdue_count += 1
            except: pass

    prompt = f"""
あなたは法学部生・受験生を支える優秀なAI秘書です。以下の実績データに基づき、ユーザーの今週の「AI秘書スコア・週次レポート」を作成してください。

【ユーザーの今週の実績】
・過去7日間に完了したタスク数: {completed_count}件
・現在残っている未完了タスク: {len(active_todos)}件
・うち、期限切れ（超過）のタスク: {overdue_count}件

【残っているタスク一覧】
{todo_list_text}

【出力ルール】
1. 冒頭に「総合評価（S, A, B, C）」を提示してください。
2. 完了数と残タスクから、今週の学習や生活のペースを客観的に評価し、モチベーションを高めるフィードバックを記載してください。
3. 超過タスクがある場合は厳しく指摘し、残タスクから来週の優先事項を1〜2つ提案してください。
"""
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=safety_config)
        embed = discord.Embed(title="📊 AI秘書スコア・週次レポート", description=response.text, color=0xf1c40f)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

# -------------------------------------------------------------------
# 🔥 新機能3: ソクラテス・メソッド（反対尋問）機能
# -------------------------------------------------------------------
@bot.tree.command(name="debate", description="🎓 厳格な法学教授（AI）とソクラテス・メソッドによる反対尋問を開始します")
@app_commands.describe(topic="議論したい法的テーマ（例: 刑事裁判におけるAI量刑判断の是非）")
async def slash_debate(interaction: discord.Interaction, topic: str):
    await interaction.response.defer()
    channel_id = interaction.channel_id
    
    # チャンネルのAI人格を強制的に「厳格な法学教授」に変更
    prof_persona = "法科大学院の厳格な教授。学生に対してソクラテス・メソッド（反対尋問）を用います。決して正解をすぐに教えず、鋭い質問を投げかけて学生の論理の矛盾や法的な穴を徹底的に詰め、思考を深めさせます。常に敬語ですが、極めて論理的で厳しいトーンです。"
    database.set_role(channel_id, prof_persona)
    
    prompt = f"""
あなたは{prof_persona}
現在、学生から以下のテーマについて議論を申し込まれました。
テーマ：「{topic}」

学生の論理的思考力を試すため、このテーマに関する最も本質的で、学生が答えに窮するような「鋭い質問」を1つだけ投げかけて、議論（反対尋問）をスタートしてください。挨拶は不要です。いきなり核心を突いてください。
"""
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=safety_config)
        
        embed = discord.Embed(title=f"🎓 ソクラテス・メソッド開始: {topic}", description=response.text, color=0xc0392b)
        embed.set_footer(text="※AIの人格が教授に変更されました。終了する場合は /role reset を実行してください。")
        await interaction.followup.send(embed=embed)
        
        # 履歴にも記録する
        database.add_message(channel_id, interaction.user.id, "user", f"【テーマ設定】{topic}についてソクラテス・メソッドで議論したい。")
        database.add_message(channel_id, bot.user.id, "model", response.text)
        
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

# --- 既存のTODOコマンド（完了処理の差し替え） ---
@bot.tree.command(name="todo_add", description="📝 期限と優先度を指定してTODOタスクを追加します")
async def slash_todo_add(interaction: discord.Interaction, task: str, deadline: str = None, priority: str = "中"):
    if deadline and not re.match(r"^\d{4}-\d{2}-\d{2}$", deadline):
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description="期限は YYYY-MM-DD 形式で入力してください。", color=0xe74c3c), ephemeral=True)
        return
    database.add_todo(interaction.user.id, task, deadline, priority)
    await interaction.response.send_message(embed=discord.Embed(title="📝 TODO 追加完了", description=f"タスク: {task}", color=0x3498db))

@bot.tree.command(name="todo_list", description="📋 現在のTODO一覧を確認します")
async def slash_todo_list(interaction: discord.Interaction):
    todos = database.get_todos(interaction.user.id, filter_type="all")
    if not todos:
        await interaction.response.send_message(embed=discord.Embed(title="📋 TODO 一覧", description="タスクはありません。", color=0x95a5a6))
        return
    desc = ""
    for t in todos:
        p_emoji = "🔴" if t[3] == "高" else "🟡" if t[3] == "中" else "🔵"
        dl = f" (📅 {t[2]})" if t[2] else ""
        desc += f"**ID:{t[0]}** | {p_emoji} [{t[3]}] **{t[1]}**{dl}\n"
    await interaction.response.send_message(embed=discord.Embed(title="📋 TODO 一覧", description=desc, color=0xf1c40f))

# 🆕 todo_doneコマンドの中身を delete_todo から complete_todo に変更
@bot.tree.command(name="todo_done", description="✅ 完了したTODOを履歴に記録して消去します")
async def slash_todo_done(interaction: discord.Interaction, todo_id: int):
    if database.complete_todo(todo_id, interaction.user.id):
        await interaction.response.send_message(embed=discord.Embed(title="✅ TODO 完了", description=f"ID:{todo_id} を完了し、実績スコアに加算しました！", color=0x2ecc71))
    else:
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description="該当のIDが見つかりません。", color=0xe74c3c))

# --- 他の既存コマンド (role_reset等) は省略せず維持 ---
@bot.tree.command(name="role", description="🎭 AIの性格を変更します")
async def slash_role(interaction: discord.Interaction, persona: str):
    if persona.lower() in ["リセット", "reset", "解除"]:
        database.delete_role(interaction.channel_id)
        await interaction.response.send_message(embed=discord.Embed(title="🔄 人格リセット", description="通常の「優秀な秘書」に戻しました。", color=0x9b59b6))
    else:
        database.set_role(interaction.channel_id, persona)
        await interaction.response.send_message(embed=discord.Embed(title="🎭 人格設定", description=f"AIを **【{persona}】** に設定しました。", color=0xe67e22))

# --- オンメッセージ処理（RAGと履歴保存） ---
@bot.event
async def on_message(message):
    if message.author.bot: return

    if bot.user.mentioned_in(message):
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

        try:
            status_msg = await message.channel.send(embed=discord.Embed(description="🤔 分析中...", color=0x34495e))
            
            prompt = f"あなたは{current_role}です。特に【関連知識】がある場合は最優先して答えてください。\n\n{history_str}\n{rag_context}\n質問: {user_text}"
            answer = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
            
            await status_msg.delete()
            await message.channel.send(embed=discord.Embed(title=f"🤖 {current_role}", description=answer.text, color=0xecf0f1))
            
            database.add_message(message.channel.id, message.author.id, "user", user_text)
            database.add_message(message.channel.id, message.author.id, "model", answer.text)
                
        except Exception as e:
            if 'status_msg' in locals(): await status_msg.delete()
            await message.channel.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

bot.run(DISCORD_TOKEN)
