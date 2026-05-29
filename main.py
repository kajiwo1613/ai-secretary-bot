import discord
from discord.ext import commands
import google.generativeai as genai
import os
from duckduckgo_search import DDGS
import requests
from bs4 import BeautifulSoup

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

# 誰でも確実に使える安定版（1.0世代）の名称に変更
model_flash = genai.GenerativeModel('gemini-pro')
model_pro = genai.GenerativeModel('gemini-pro')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} 起動成功")

@bot.command()
async def ask(ctx, *, question):
    try:
        response = model_flash.generate_content(f"あなたは優秀なAI秘書です。\n質問：{question}")
        await ctx.send(response.text)
    except Exception as e:
        await ctx.send(f"エラー原因：{e}")

@bot.command()
async def pro(ctx, *, question):
    try:
        response = model_pro.generate_content(f"あなたは優秀なAI秘書です。論理的に答えてください。\n質問：{question}")
        await ctx.send(response.text)
    except Exception as e:
        await ctx.send(f"エラー原因：{e}")

@bot.command()
async def search(ctx, *, question):
    await ctx.send("🔍 確証のある情報を得るため、複数のウェブサイトの中身を直接読み込んでいます。完了まで少し時間がかかります...")
    
    try:
        search_results = DDGS().text(question, region='wt-wt', safesearch='off', max_results=5)
        
        if not search_results:
            await ctx.send("関連する検索結果が見つかりませんでした。")
            return

        context = "【読み込んだウェブサイトの実際の情報】\n\n"
        
        for index, result in enumerate(search_results):
            url = result.get('href')
            title = result.get('title')
            context += f"--- 情報源 {index+1}: {title} ---\nURL: {url}\n"
            
            try:
                response = requests.get(url, timeout=3)
                response.encoding = response.apparent_encoding
                soup = BeautifulSoup(response.text, 'html.parser')
                page_text = soup.get_text(separator=' ', strip=True)
                context += f"内容: {page_text[:2000]}\n\n"
            except Exception:
                context += f"内容: {result.get('body')}\n\n"

        prompt = f"""
        あなたは優秀なAIリサーチャーです。以下の「読み込んだウェブサイトの実際の情報」という事実情報のみに基づいて、ユーザーの質問に正確に答えてください。
        憶測や不確かな情報は完全に排除し、サイトから得られた具体的・現実的な情報のみを提示してください。
        どの情報源（タイトルやURL）からその事実が得られたのかも、合わせて記載してください。
        
        {context}
        
        【ユーザーの質問】: {question}
        """
        
        await ctx.send("🧠 サイトの熟読が完了しました。現在、情報を論理的に分析・統合しています...")
        
        answer = model_pro.generate_content(prompt)
        await ctx.send(answer.text)

    except Exception as e:
        await ctx.send(f"検索エラー原因：{e}")

bot.run(DISCORD_TOKEN)
