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
from pydantic import BaseModel, Field

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

safety_config = types.GenerateContentConfig(
    safety_settings=[
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    ]
)

# 🤖 ② 逆算型マイルストーン自動分解用のデータ構造定義（Structured Outputs用）
class Milestone(BaseModel):
    task: str = Field(description="細分化された具体的なタスク内容。法学の学習ステップとして現実的なもの。")
    deadline: str = Field(description="このタスクの期限。YYYY-MM-DD形式。目標締切から逆算して妥当な日付。")
    priority: str = Field(description="タスクの優先度。'高'、'中'、'低' のいずれか。")

class MilestoneList(BaseModel):
    milestones: list[Milestone]

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
    if not reminder_loop.is_running():
        reminder_loop.start()
    print(f"{bot.user} 起動成功 - モーニングブリーフィング＆マイルストーン生成搭載版")

# ⏰ 定期ループ（1時間ごと）内でリマインダーと「朝7時のモーニング・ブリーフィング」を両方処理
@tasks.loop(hours=1)
async def reminder_loop():
    await bot.wait_until_ready()
    now = datetime.now()
    
    # ------------------------------------
    # 功能①: 毎朝7時のモーニング・ブリーフィング処理
    # ------------------------------------
    if now.hour == 7:
        user_ids = database.get_all_users_with_todos()
        
        # 送信対象のユーザーが1人でも存在する場合、Tavily APIの消費を節約するため1回だけ検索
        if user_ids:
            weather_context = ""
            try:
                # 確証のある地域インフラ情報（天気）を取得
                url = "https://api.tavily.com/search"
                payload = {"api_key": TAVILY_API_KEY, "query": "岡山市 今日の天気 気温 傘が必要か", "search_depth": "basic", "max_results": 2}
                search_data = requests.post(url, json=payload).json()
                if "results" in search_data:
                    weather_context = "【本日の岡山市の気象事実情報】\n"
                    for r in search_data["results"]:
                        weather_context += f"{r.get('content')}\n"
            except:
                weather_context = "【本日の気象事実情報】気象データの取得に一時的に失敗しました。\n"

            for u_id in user_ids:
                # 本日すでに送信済みの場合は重複防止のためスキップ
                if not database.check_and_record_briefing(u_id):
                    continue
                    
                user = bot.get_user(u_id)
                if not user:
                    try: user = await bot.fetch_user(u_id)
                    except: continue
                
                # そのユーザーの本日締切のTODOを取得
                today_todos = database.get_todos(u_id, filter_type="today")
                todo_str = "【本日のユーザーの締切TODO】\n"
                if not today_todos:
                    todo_str += "・本日が締切の最優先タスクはありません。順調です！\n"
                else:
                    for t in today_todos:
                        todo_str += f"・[優先度:{t[3]}] {t[1]}\n"
                
                # 全件のTODOも参考として数件渡す
                all_todos = database.get_todos(u_id, filter_type="all")[:5]
                todo_str += "\n【直近のその他のTODO（参考）】\n"
                for t in all_todos:
                    if t not in today_todos:
                        dl = f"(期限: {t[2]})" if t[2] else ""
                        todo_str += f"・[{t[3]}] {t[1]} {dl}\n"

                briefing_prompt = f"""
あなたは優秀な個人のAI秘書です。毎朝の定時報告（モーニング・ブリーフィング）を作成してください。
提供された『気象事実情報』と『ユーザーのTODOリスト』を完全に分析し、ユーザーが最高のスタートを切れるように知的に案内してください。

{weather_context}

{todo_str}

【報告のルール】
1. 挨拶から始め、天気の事実に基づいた具体的なアドバイス（傘が必要か、服の温かさなど）を優しく添えてください。
2. 本日締切の最優先タスクを強調し、今日をどう過ごすべきか秘書として現実的な行動指針を提案してください。
3. トーンは理知的、誠実、かつモチベーションを高めるプロの秘書スタイル（敬語）を徹底してください。架空の事実は絶対に含めないでください。
"""
                try:
                    response = client.models.generate_content(model='gemini-2.5-flash', contents=briefing_prompt, config=safety_config)
                    embed = discord.Embed(title="🌤️ モーニング・ブリーフィング", description=response.text, color=0x3498db)
                    embed.set_footer(text=f"報告日時: {now.strftime('%Y-%m-%d %H:%M')}")
                    await user.send(embed=embed)
                except discord.Forbidden: pass
                except Exception: pass

    # ------------------------------------
    # 既存機能: 自動リマインダー（24時間前/3時間前）
    # ------------------------------------
    active_todos = database.get_all_active_todos()
    for todo in active_todos:
        todo_id, user_id, task, deadline_str, priority, reminded_24h, reminded_3h = todo
        try:
            deadline = datetime.strptime(f"{deadline_str} 23:59:59", "%Y-%m-%d %H:%M:%S")
        except ValueError: continue
            
        time_left = deadline - now
        hours_left = time_left.total_seconds() / 3600
        
        user = bot.get_user(user_id)
        if not user:
            try: user = await bot.fetch_user(user_id)
            except: continue
            
        if 0 < hours_left <= 24 and reminded_24h == 0:
            p_emoji = "🔴" if priority == "高" else "🟡" if priority == "中" else "🔵"
            embed = discord.Embed(title="⚠️ 【AI秘書】TODO締切24時間前通知", description=f"タスク **「{task}」** の締切まで残り24時間を切りました！", color=0xe67e22)
            embed.add_field(name="期限日", value=deadline_str, inline=True)
            embed.add_field(name="優先度", value=f"{p_emoji} {priority}", inline=True)
            try:
                await user.send(embed=embed)
                database.update_todo_reminded(todo_id, "reminded_24h", 1)
            except discord.Forbidden: pass
            
        if 0 < hours_left <= 3 and reminded_3h == 0:
            p_emoji = "🔴" if priority == "高" else "🟡" if priority == "中" else "🔵"
            embed = discord.Embed(title="🚨 【AI秘書】TODO締切直前通知 (残り3時間)", description=f"タスク **「{task}」** の締切まで残り3時間を切っています！お急ぎください！", color=0xe74c3c)
            embed.add_field(name="期限日", value=deadline_str, inline=True)
            embed.add_field(name="優先度", value=f"{p_emoji} {priority}", inline=True)
            try:
                await user.send(embed=embed)
                database.update_todo_reminded(todo_id, "reminded_3h", 1)
            except discord.Forbidden: pass

# 🧠 ② 新機能: 逆算型マイルストーン生成（Structured Outputsによる完全自動DB一括登録）
@bot.tree.command(name="plan_generate", description="🧠 大きな目標から逆算して、締切付きの細分化されたTODOリストをAI秘書が自動生成・一括登録します")
@app_commands.describe(goal="大きな目標・タスク（例: 民法過去問10年分を完成させる、卒業論文提出）", deadline="最終締切日 (YYYY-MM-DD形式、例: 2026-07-15)")
async def slash_plan_generate(interaction: discord.Interaction, goal: str, deadline: str):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", deadline):
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description="締切日は `YYYY-MM-DD` 形式（例: 2026-07-15）で入力してください。", color=0xe74c3c), ephemeral=True)
        return
        
    await interaction.response.defer()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 構造化JSON出力を強制するための専用Configを構築
    milestone_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=MilestoneList,
        safety_settings=safety_config.safety_settings
    )
    
    prompt = f"""
あなたは法学部生や受験生をナビゲートする超一流のプロジェクトマネージャー兼秘書です。
ユーザーが掲げた大きな目標「{goal}」を、最終締切日「{deadline}」までに確実に、かつ無理なく達成できるよう、今日「{today_str}」からの進捗マイルストーンを3〜5個の具体的な子タスクに分解して自動生成してください。

【逆算の設計指針】
1. 最終締切日から現在に向けて逆算し、各マイルストーンの妥当な「deadline（期限）」を割り振ってください（すべてYYYY-MM-DD形式）。
2. 各ステップは「民法総則のインプット」「過去問〇年〜〇年の起案」など、具体的で行動に移しやすいタスク名にしてください。
3. 終盤のタスクほど優先度（priority）を高めに設定するなど、現実的な緩急をつけてください。
"""

    try:
        current_model = channel_modes.get(interaction.channel_id, 'gemini-2.5-flash')
        # Structured Outputsの呼び出し
        response = client.models.generate_content(model=current_model, contents=prompt, config=milestone_config)
        
        # Pydanticモデルを使って安全にパース
        milestones_data = MilestoneList.model_validate_json(response.text)
        
        desc_text = f"目標 **「{goal}」** に向けたマイルストーンを解析しました。\n以下のタスクをあなたのTODOリストに自動一括登録しました！\n\n"
        
        for m in milestones_data.milestones:
            # データベースへ自動登録（シームレスに朝のブリーフィングやリマインダーの対象になります）
            database.add_todo(interaction.user.id, f"[{goal[:10]}...] {m.task}", m.deadline, m.priority)
            
            p_emoji = "🔴" if m.priority == "高" else "🟡" if m.priority == "中" else "🔵"
            desc_text += f"{p_emoji} **{m.task}**\n   📅 期限: {m.deadline} | 優先度: {m.priority}\n\n"
            
        embed = discord.Embed(title="🧠 逆算型マイルストーン生成・一括登録完了", description=desc_text, color=0x9b59b6)
        embed.set_footer(text="毎朝のブリーフィングおよび締切前通知で秘書が進行をサポートします。")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"プラン生成中にエラーが発生しました: `{str(e)[:500]}`", color=0xe74c3c))

# 📝 既存コマンド（todo_add）
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

# 📋 既存コマンド（todo_list）
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

# 🧠 既存コマンド（study_plan）
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
    prompt = f"... (中略、前回のプロンプトと同様) ...\n{todo_context}\n{history_str}"
    try:
        response = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
        embed = discord.Embed(title="🧠 AI学習コーチによる本日のプラン", description=response.text, color=0x9b59b6)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

# 🍽️ 既存コマンド（gourmet_search）
@bot.tree.command(name="gourmet_search", description="🍽️ 指定した土地の美味しいお店や、その土地ならではの料理が食べられる店を検索します")
@app_commands.describe(location="場所・エリア", keyword="ジャンルや食べたいもの")
async def slash_gourmet_search(interaction: discord.Interaction, location: str, keyword: str):
    await interaction.response.defer()
    search_query = f"{location} {keyword} おすすめ 美味しい 店 郷土料理 予約URL"
    url = "https://api.tavily.com/search"
    payload = {"api_key": TAVILY_API_KEY, "query": search_query, "search_depth": "advanced", "max_results": 5}
    try:
        res = requests.post(url, json=payload)
        search_data = res.json()
        context = "【ウェブ検索から得られた店舗情報】\n\n"
        if "results" in search_data:
            for r in search_data["results"]: context += f"URL: {r.get('url')}\n内容: {r.get('content')}\n\n"
        current_model = channel_modes.get(interaction.channel_id, 'gemini-2.5-flash')
        prompt = f"ユーザーエリア「{location}」で「{keyword}」が楽しめる名店を検索結果を元に紹介してください。\n{context}"
        response = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
        await interaction.followup.send(embed=discord.Embed(title=f"🍽️ {location} おすすめ店舗案内", description=response.text, color=0xe67e22))
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

# 📅 既存コマンド（gourmet_reserve_helper）
@bot.tree.command(name="gourmet_reserve_helper", description="📅 飲食店の予約予定を秘書に登録し、自動リマインダーを設定します")
async def slash_gourmet_reserve_helper(interaction: discord.Interaction, shop_name: str, date: str, time: str, num_people: int):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description="日付は `YYYY-MM-DD` 形式で入力してください。", color=0xe74c3c), ephemeral=True)
        return
    task_text = f"【飲食店予約】{shop_name} (⏰ {time}〜 / 👥 {num_people}名)"
    database.add_todo(interaction.user.id, task_text, deadline=date, priority="高")
    await interaction.response.send_message(embed=discord.Embed(title="📅 飲食店予約・リマインダー登録完了", description=f"店舗: {shop_name}\n日時: {date} {time}", color=0x2ecc71))

# 🎬 既存コマンド（movie_search）
@bot.tree.command(name="movie_search", description="🎬 指定したエリアの映画館・上映中の映画スケジュールを検索します")
async def slash_movie_search(interaction: discord.Interaction, location: str, keyword: str = ""):
    await interaction.response.defer()
    search_query = f"{location} 映画館 上映スケジュール {keyword} 予約 公式URL"
    url = "https://api.tavily.com/search"
    payload = {"api_key": TAVILY_API_KEY, "query": search_query, "search_depth": "advanced", "max_results": 5}
    try:
        res = requests.post(url, json=payload)
        search_data = res.json()
        context = "【ウェブ検索から得られた映画情報】\n\n"
        if "results" in search_data:
            for r in search_data["results"]: context += f"URL: {r.get('url')}\n内容: {r.get('content')}\n\n"
        current_model = channel_modes.get(interaction.channel_id, 'gemini-2.5-flash')
        prompt = f"エリア「{location}」の映画・映画館情報を紹介してください。キーワード「{keyword}」\n{context}"
        response = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
        await interaction.followup.send(embed=discord.Embed(title=f"🎬 {location} 映画スケジュール案内", description=response.text, color=0x9b59b6))
    except Exception as e:
        await interaction.followup.send(embed=discord.Embed(title="⚠️ エラー", description=f"`{str(e)[:500]}`", color=0xe74c3c))

# 🎟️ 既存コマンド（movie_reserve_helper）
@bot.tree.command(name="movie_reserve_helper", description="🎟️ 映画の予約時間を秘書に登録し、自動リマインダーを設定します")
async def slash_movie_reserve_helper(interaction: discord.Interaction, movie_title: str, theater: str, date: str, time: str):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ エラー", description="日付は `YYYY-MM-DD` 形式で入力してください。", color=0xe74c3c), ephemeral=True)
        return
    task_text = f"【映画鑑賞】『{movie_title}』 @ {theater} (⏰ {time}〜)"
    database.add_todo(interaction.user.id, task_text, deadline=date, priority="高")
    await interaction.response.send_message(embed=discord.Embed(title="🎟️ 映画チケット・リマインダー登録完了", description=f"作品: 『{movie_title}』\n劇場: {theater}\n日時: {date} {time}", color=0x3498db))

# 🎭 既存コマンド（role）
@bot.tree.command(name="role", description="🎭 AIの性格（人格）を変更します")
async def slash_role(interaction: discord.Interaction, persona: str):
    if persona.lower() in ["リセット", "reset", "解除"]:
        database.delete_role(interaction.channel_id)
        await interaction.response.send_message(embed=discord.Embed(title="🔄 人格リセット", description="AIの人格を通常の「優秀な秘書」に戻しました。", color=0x9b59b6))
    else:
        database.set_role(interaction.channel_id, persona)
        await interaction.response.send_message(embed=discord.Embed(title="🎭 人格設定", description=f"AIの人格を **【{persona}】** に設定しました。", color=0xe67e22))

# ⚖️ 既存コマンド（knowledge_add）
@bot.tree.command(name="knowledge_add", description="⚖️ AI専用の知識（論証集や六法など）を登録します")
async def slash_knowledge_add(interaction: discord.Interaction, keyword: str, content: str):
    database.add_knowledge(keyword, content)
    embed = discord.Embed(title="⚖️ 知識データベース登録完了", description=f"キーワード **【{keyword}】** で知識を記憶しました。\n内容先頭: {content[:100]}...", color=0x1abc9c)
    await interaction.response.send_message(embed=embed)

# 🧠 既存コマンド（memory）
@bot.tree.command(name="memory", description="🧠 【管理者用】現在の長期記憶を確認します")
async def slash_memory(interaction: discord.Interaction):
    long_term = long_term_memories.get(interaction.channel_id, "まだ長期記憶はありません。")
    await interaction.response.send_message(embed=discord.Embed(title="🧠 AIの現在の脳内メモリ", description=long_term, color=0x1abc9c))

# 💬 メッセージ受信イベント（オンメッセージ）
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

                prompt = f"あなたは{current_role}es。特に【登録知識】がある場合は最優先して答えてください。\n\n{history_str}\n{rag_context}\n{context}\n質問: {user_text}"
                await status_msg.edit(embed=discord.Embed(description="🧠 情報統合中...", color=0x8e44ad))
                answer = client.models.generate_content(model=current_model, contents=prompt, config=safety_config)
                await status_msg.delete()
                await message.channel.send(embed=discord.Embed(title=f"🤖 統合回答", description=answer.text, color=0xecf0f1))
                add_history(
