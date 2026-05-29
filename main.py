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
    print(f"{bot.user} 起動成功 - 軽量高精度RAGエンジン搭載版")

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

# 🌟 新機能：RAG用知識登録コマンド
@bot.tree.command(name="knowledge_add", description="⚖️ AI専用の知識（論証集や六法のテキストなど）をデータベースに登録します")
async def slash_knowledge_add(interaction: discord.Interaction, keyword: str, content: str):
    database.add_knowledge(keyword, content)
    embed = discord.Embed(
        title="⚖️ 知識データベース登録完了",
        description=f"キーワード **【{keyword}】** で以下の知識を記憶しました。今後、質問にこの単語が含まれるとAIが自動的にこの内容を参照して回答します。\n\n
http://googleusercontent.com/immersive_entry_chip/0

---

### 🧪 新しい「自分専用法律AI」のテスト手順

Renderの再起動が終わり、前回の「最後の儀式（スラッシュコマンドの再招待）」が済んでいれば、入力欄に `/knowledge_add` という超強力なコマンドが出現します。

以下の手順でテストを回してみてください。別次元の知性が宿ります。

**ステップ1：あなただけの「公式論証」をAIに記憶させる**
Discordで以下のようにスラッシュコマンドを打ち込み、送信します。

* **keyword:** `共謀共同正犯`
* **content:** `共謀共同正犯が成立するためには、①２人以上の者が、特定の犯罪を行うことについて共同意思の下に一体となって互いに利用し合って犯罪を実行する合意（共謀）をし、②共謀者のいずれかがその共謀に基づいて犯罪を実行したこと（実行行為）が必要である。その本質は、自己の不法として他人の行為を道具のごとく利用する点にある。`

**ステップ2：わざとアバウトに質問して、RAGを起動させる**
ボットに向けて、普通にメンションで話しかけます。

> `@AI秘書 共謀共同正犯って、結局どういう時に成立するんだっけ？簡単に教えて。`

システムが自動的に「共謀共同正犯」という単語を検知し、`📚 登録された専門知識データベース（RAG）を参照しています...` という専用のEmbedを出しながら、**あなたが今登録したばかりの①や②の定義、そして「自己の不法として他人の行為を利用する」という全く同じフレーズを正確に用いた回答**を生成してくれたら、**完全大成功**です！

これで、ネットの適当な解説ではなく、あなたが覚えたい「ガチの論証集や教科書の記述」をそのまま喋る最強の法学パートナーの基盤が整いました。ぜひ体験してみてください！
