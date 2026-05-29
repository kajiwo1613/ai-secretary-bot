import discord
from discord.ext import commands
import google.generativeai as genai
import os

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

# === 今使えるAIを自動で探す最強のコード ===
available_model = None
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        available_model = m.name
        break # 見つけたら探すのをやめる

# 見つけたAIをセットする
model = genai.GenerativeModel(available_model)
# =======================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    # 起動した時に、どのAIを見つけたかLogsに表示させます
    print(f"{bot.user} 起動成功！(発見したAI: {available_model})")

@bot.command()
async def ask(ctx, *, question):
    try:
        response = model.generate_content(
            f"あなたは優秀なAI秘書です。以下の質問に答えてください。\n\n質問：{question}"
        )
        await ctx.send(response.text)
    except Exception as e:
        await ctx.send(f"エラー原因:{e}")

bot.run(DISCORD_TOKEN)
