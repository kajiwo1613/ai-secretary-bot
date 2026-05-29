import discord
from discord import app_commands # 🌟 Slash Command用の部品
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

# 🌟 1日あたりの無料利用上限回数（収益化時のプラン分けに直結します）
DAILY_LIMIT = 20 

def get_history_text(channel_id):
    long_term = long_term_memories.get(channel_id, "")
    history = channel_histories.get(channel_id, [])
    text = ""
    if long_term: text += f"【重要：長期記憶】\n{long_term}\n\n"
    if history:
        text += "【直近の会話履歴】\n"
        for h in history:
            role_name = "ユーザー" if h["role"] == "user" else "AI"
            text += f"{role_name}: {h['text']}\n"
        text += "（履歴ここまで）\n\n"
    return text

def add_history(channel_id, role, text):
    if channel_id not in channel_histories: channel_histories[channel_id] = []
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
    # 🌟 起動時にSlash CommandをDiscordサーバーに同期（登録）する
    await bot.tree.sync()
    print(f"{bot.user} 起動成功 - Slash Command & SaaS基盤版")

# ==========================================
# 🌟 Slash Commands (本物のSaaSアプリ化)
# ==========================================

@bot.tree.command(name="todo_add", description="📝 新しいTODOタスクを追加します")
async def slash_todo_add(interaction: discord.Interaction, task: str):
    database.add_todo(interaction.user.id, task)
    embed = discord.Embed(title="📝 TODO 追加", description=f"タスク: **{task}**", color=0x3498db)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="todo_list", description="📋 現在のTODO一覧を確認します")
async def slash_todo_list(interaction: discord.Interaction):
    todos = database.get_todos(interaction.user.id)
    if not todos:
        embed = discord.Embed(title="📋 TODO 一覧", description="現在登録されているタスクはありません。", color=0x95a5a6)
        await interaction.response.send_message(embed=embed)
        return
    
    desc = ""
    for t in todos: desc += f"**ID:{t[0]}** - {t[1]}\n"
    embed = discord.Embed(title="📋 TODO 一覧", description=desc, color=0xf1c40f)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="todo_done", description="✅ 完了したTODOを削除します")
async def slash_todo_done(interaction: discord.Interaction, todo_id: int):
    if database.delete_todo(todo_id, interaction.user.id):
        embed = discord.Embed(title="✅ TODO 完了", description=f"ID:{todo_id} のタスクを完了しました！お疲れ様です。", color=0x2ecc71)
    else:
        embed = discord.Embed(title="⚠️ エラー", description="指定されたIDが見つかりません。", color=0xe74c3c)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="role", description="🎭 AIの性格（人格）を変更します")
async def slash_role(interaction: discord.Interaction, persona: str):
    if persona.lower() in ["リセット", "reset", "解除"]:
        database.delete_role(interaction.channel_id)
        embed = discord.Embed(title="🔄 人格リセット", description="AIの人格を通常の「優秀な秘書」に戻しました。", color=0x9b59b6)
    else:
        database.set_role(interaction.channel_id, persona)
        embed = discord.Embed(title="🎭 人格設定", description=f"AIの人格を **【{persona}】** に設定・記憶しました。", color=0xe67e22)
    await interaction.response.send_message(embed=embed)

# 🌟 管理者機能（Memory確認）
@bot.tree.command(name="memory", description="🧠 【管理者用】AIが現在この部屋で記憶している長期記憶を確認します")
async def slash_memory(interaction: discord.Interaction):
    long_term = long_term_memories.get(interaction.channel_id, "まだ長期記憶はありません。")
    embed = discord.Embed(title="🧠 AIの現在の脳内メモリ", description=long_term, color=0x1abc9c)
    await interaction.response.send_message(embed=embed)

# ==========================================
# 💬 AI会話エンジン (メンションで動作)
# ==========================================

@bot.event
async def on_message(message):
    if message.author.bot: return

    if bot.user.mentioned_in(message):
        # 🌟 使用回数（Rate Limit）のチェック
        is_allowed, current_count = database.check_and_increment_usage(message.author.id, limit=DAILY_LIMIT)
        if not is_allowed:
            embed = discord.Embed(title="🛑 本日の利用上限に達しました", description=f"無料プランの1日{DAILY_LIMIT}回の上限に達しました。明日またご利用いただくか、プレミアムプランをご検討ください。", color=0xe74c3c)
            await message.channel.send(embed=embed)
            return

        user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
        current_model = channel_modes.get(message.channel.id, 'gemini-2.5-flash')
        
        db_role = database.get_role(message.channel.id)
        current_role = db_role if db_role else "優秀なAI秘書"
        
        history_str = get_history_text(message.channel.id)

        todos = database.get_todos(message.author.id)
        if todos:
            todo_text = "【ユーザーの現在の未完了TODO（これを踏まえてサポートしてください）】\n"
            for t in todos: todo_text += f"・{t[1]}\n"
            history_str = todo_text + "\n" + history_str

        # （中略：画像・PDF・音声の処理はそのまま維持）

        # 文字のみの通常会話
        if not user_text:
            await message.channel.send("はい、何でしょうか？")
            return

        # 🌟 UI強化：思考中メッセージもEmbed化してスタイリッシュに
        status_msg = await message.channel.send(embed=discord.Embed(description="🤔 情報を分析・検索しています...", color=0x34495e))
        
        try:
            intent_check = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"以下の文章が事実確認のウェブ検索が必要な質問か判定し、必要ならYES、不要ならNOとだけ答えてください。\n文章：{user_text}"
            )
            await asyncio.sleep(3)
            
            if "YES" in intent_check.text.upper():
                await status_msg.edit(embed=discord.Embed(description="🔍 外部リサーチAIで事実確認を行っています...", color=0x2980b9))
                url = "https://api.tavily.com/search"
                payload = {"api_key": TAVILY_API_KEY, "query": user_text, "search_depth": "advanced", "max_results": 5, "include_raw_content": False}
                search_data = requests.post(url, json=payload).json()
                
                context = "【読み込んだウェブサイトの情報】\n\n"
                if "results" in search_data:
                    for index, result in enumerate(search_data["results"]):
                        context += f"--- 情報源 {index+1}: {result.get('title')} ---\nURL: {result.get('url')}\n内容: {result.get('content')}\n\n"

                prompt = f"あなたは{current_role}です。事実情報のみに基づき正確に答えてください。\n{history_str}\n{context}\n質問: {user_text}"
                await status_msg.edit(embed=discord.Embed(description="🧠 情報の分析・統合中...", color=0x8e44ad))
                answer = client.models.generate_content(model=current_model, contents=prompt)
                await status_msg.delete()
                
                # 🌟 AIの返答をEmbed化して「アプリ感」を出す
                response_embed = discord.Embed(title=f"🤖 AIリサーチ回答", description=answer.text, color=0xecf0f1)
                await message.channel.send(embed=response_embed)
                
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", answer.text)
                
            else:
                prompt = f"あなたは{current_role}です。【これまでの会話履歴】を踏まえて具体的かつ現実的に答えてください。\n{history_str}\n質問：{user_text}"
                response = client.models.generate_content(model=current_model, contents=prompt)
                await status_msg.delete()
                
                # 🌟 AIの返答をEmbed化
                response_embed = discord.Embed(title=f"🤖 {current_role}", description=response.text, color=0xecf0f1)
                await message.channel.send(embed=response_embed)
                
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)

            # --- 裏側での記憶整理 ---
            history = channel_histories.get(message.channel.id, [])
            if len(history) >= 8:
                try:
                    current_long = long_term_memories.get(message.channel.id, "")
                    hist_text = "\n".join([f"{h['role']}: {h['text']}" for h in history])
                    p = f"あなたは裏方の記憶整理係です。これまでの【長期記憶】と【直近の会話】を統合し、ユーザーの興味・前提知識・重要な話題を最新の【要約記憶】として箇条書きで更新してください。\n\n【長期記憶】\n{current_long}\n\n【直近の会話】\n{hist_text}"
                    await asyncio.sleep(3)
                    res = client.models.generate_content(model='gemini-2.5-flash', contents=p)
                    if res.text:
                        long_term_memories[message.channel.id] = res.text
                        channel_histories[message.channel.id] = channel_histories[message.channel.id][-4:]
                except Exception: pass

        except Exception as e:
            await status_msg.delete()
            err_embed = discord.Embed(title="⚠️ エラーが発生しました", description=f"処理中に問題が発生しました。\n`{str(e)[:500]}`", color=0xe74c3c)
            await message.channel.send(embed=err_embed)

bot.run(DISCORD_TOKEN)
