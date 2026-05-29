import discord
from discord.ext import commands
import google.generativeai as genai
import os

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Geminiの設定
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} 起動成功")

@bot.command()
async def ask(ctx, *, question):
    try:
        response = model.generate_content(
            f"あなたは優秀なAI秘書です。以下の質問に答えてください。\n\n質問：{question}"
        )
        await ctx.send(response.text)
    except Exception as e:
        await ctx.send(f"エラー原因:[e]")

bot.run(DISCORD_TOKEN)
