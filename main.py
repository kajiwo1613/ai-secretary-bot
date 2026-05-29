import discord
from discord.ext import commands
from google import genai
import os
import requests

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} 起動成功")

@bot.command()
async def ask(ctx, *, question):
    try:
        # 安定性とスピードに優れた 2.0-flash を採用
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=f"あなたは優秀なAI秘書です。\n質問：{question}"
        )
        if response.text:
            for i in range(0, len(response.text), 1900):
                await ctx.send(response.text[i:i+1900])
    except Exception as e:
        # エラーが長くても1000文字でカットして強制送信！
        await ctx.send(f"エラー原因：{str(e)[:1000]}... (省略)")

@bot.command()
async def pro(ctx, *, question):
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=f"あなたは優秀なAI秘書です。論理的に答えてください。\n質問：{question}"
        )
        if response.text:
            for i in range(0, len(response.text), 1900):
                await ctx.send(response.text[i:i+1900])
    except Exception as e:
        await ctx.send(f"エラー原因：{str(e)[:1000]}... (省略)")

@bot.command()
async def search(ctx, *, question):
    await ctx.send("🔍 確証のある情報を得るため、専用の外部リサーチAIを使って事実確認を行っています。少々お待ちください...")
    try:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": question,
            "search_depth": "advanced",
            "max_results": 5,
            "include_raw_content": False
        }
        response = requests.post(url, json=payload)
        search_data = response.json()
        
        if "results" not in search_data or len(search_data["results"]) == 0:
            await ctx.send("関連する検索結果が見つかりませんでした。")
            return
            
        context = "【読み込んだウェブサイトの実際の情報】\n\n"
        for index, result in enumerate(search_data["results"]):
            title = result.get("title", "無題")
            link = result.get("url", "URLなし")
            content = result.get("content", "")
            context += f"--- 情報源 {index+1}: {title} ---\nURL: {link}\n内容: {content}\n\n"

        prompt = f"""
        あなたは優秀なAIリサーチャーです。以下の事実情報のみに基づいて、ユーザーの質問に正確に答えてください。
        
        {context}
        
        【ユーザーの質問】: {question}
        """
        
        await ctx.send("🧠 リサーチが完了しました。現在、得られた事実を論理的に分析・統合しています...")
        
        # 安定性とスピードに優れた 2.0-flash を採用
        answer = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt
        )
        
        if answer.text:
            for i in range(0, len(answer.text), 1900):
                await ctx.send(answer.text[i:i+1900])
        else:
            await ctx.send("⚠️ 検索結果の分析に失敗しました。回答が空です。")

    except Exception as e:
        # ここが今回の最大の原因！長すぎるエラー文を1000文字でカット！
        await ctx.send(f"検索エラー原因：{str(e)[:1000]}... (省略)")

bot.run(DISCORD_TOKEN)
