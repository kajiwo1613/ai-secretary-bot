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

# 🌟 究極の診断コマンド：Googleに直接、使えるAIの名前リストを聞き出す！
@bot.command()
async def models(ctx):
    await ctx.send("🔄 Googleのシステムに、現在使えるAIの名前リストを直接問い合わせています...")
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
        response = requests.get(url)
        data = response.json()
        
        if "models" in data:
            valid_names = []
            for m in data["models"]:
                if "supportedGenerationMethods" in m and "generateContent" in m["supportedGenerationMethods"]:
                    valid_names.append(m["name"].replace("models/", ""))
            
            if valid_names:
                msg = "\n".join(valid_names)
                await ctx.send(f"✅ あなたの鍵で現在使えるAIのリストです！\n```text\n{msg}\n```\nこの中から好きな名前を選んで main.py に設定すれば、100%確実に動きます！")
            else:
                await ctx.send("⚠️ 利用可能なAIが見つかりません。APIキーの設定を確認してください。")
        else:
             await ctx.send(f"⚠️ エラー: {data}")
    except Exception as e:
        await ctx.send(f"通信エラー: {e}")

@bot.command()
async def ask(ctx, *, question):
    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=f"あなたは優秀なAI秘書です。\n質問：{question}"
        )
        await ctx.send(response.text)
    except Exception as e:
        await ctx.send(f"エラー原因：{e}")

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
        
        # ※ここでエラーが出る場合は、!models で出た名前にここを書き換えます
        answer = client.models.generate_content(
            model='gemini-1.5-pro',
            contents=prompt
        )
        await ctx.send(answer.text)

    except Exception as e:
        await ctx.send(f"検索エラー原因：{e}")

bot.run(DISCORD_TOKEN)
