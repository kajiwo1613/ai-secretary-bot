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
    await bot.tree.sync()
    print(f"{bot.user} 起動成功 - タブレット対応 RAG＆SaaS基盤版")

# ==========================================
# 🌟 Slash Commands (UI/UX)
# ==========================================

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
    desc = "".join([f"**ID:{t[0]}** - {t[1]}\n" for t in todos])
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

# 🌟 RAG用知識登録コマンド (コピペ分断エラー対策済みの安全な書き方)
@bot.tree.command(name="knowledge_add", description="⚖️ AI専用の知識（論証集や六法など）を登録します")
async def slash_knowledge_add(interaction: discord.Interaction, keyword: str, content: str):
    database.add_knowledge(keyword, content)
    
    # 長い文章を短いパーツに分けて結合し、改行バグを防ぎます
    desc_part1 = f"キーワード **【{keyword}】** で知識を記憶しました。\n"
    desc_part2 = "今後、質問にこの単語が含まれるとAIが自動参照します。\n\n"
    desc_part3 = f"
http://googleusercontent.com/immersive_entry_chip/0
