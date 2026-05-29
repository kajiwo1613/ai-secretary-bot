import discord
from discord.ext import commands
from google import genai
import os

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 最新のAIツールを準備
client_ai = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} 最新システムで起動成功！")

@bot.command()
async def ask(ctx, *, question):
    try:
        # 最新の書き方でAIに質問を投げる
        response = client_ai.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"あなたは優秀なAI秘書です。以下の質問に答えてください。\n\n質問：{question}"
        )
        await ctx.send(response.text)
    except Exception as e:
        await ctx.send(f"エラー原因:{e}")

bot.run(DISCORD_TOKEN)
