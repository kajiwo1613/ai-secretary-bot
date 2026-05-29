import discord
from discord.ext import commands
from google import genai
import os
import requests
from PIL import Image
import io
import pypdf
import asyncio

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

channel_modes = {}
channel_histories = {}

def get_history_text(channel_id):
    history = channel_histories.get(channel_id, [])
    if not history:
        return ""
    text = "【これまでの会話履歴（文脈）】\n"
    for h in history:
        role_name = "ユーザー" if h["role"] == "user" else "AI秘書"
        text += f"{role_name}: {h['text']}\n"
    text += "（履歴ここまで。この流れを踏まえて会話してください）\n\n"
    return text

def add_history(channel_id, role, text):
    if channel_id not in channel_histories:
        channel_histories[channel_id] = []
    channel_histories[channel_id].append({"role": role, "text": text})
    if len(channel_histories[channel_id]) > 10:
        channel_histories[channel_id].pop(0)

async def send_response(channel, text):
    if not text:
        return
    if len(text) > 1500:
        with io.BytesIO(text.encode('utf-8-sig')) as f:
            await channel.send(
                content="📄 回答が長文（1,500文字以上）になったため、確認しやすいようテキストファイルに出力しました：",
                file=discord.File(f, filename="ai_secretary_report.txt")
            )
    else:
        await channel.send(text)

@bot.event
async def on_ready():
    print(f"{bot.user} 起動成功 - 深呼吸（クールダウン）機能搭載版")

@bot.command()
async def mode(ctx, level: str):
    if level.lower() == "pro":
        channel_modes[ctx.channel.id] = 'gemini-2.5-pro'
        await ctx.send("🧠 このチャンネルの頭脳を【Gemini 2.5 Pro（高精度モード）】に設定しました。")
    elif level.lower() == "flash":
        channel_modes[ctx.channel.id] = 'gemini-2.5-flash'
        await ctx.send("⚡ このチャンネルの頭脳を【Gemini 2.5 Flash（高速モード）】に設定しました。")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith('!'):
        await bot.process_commands(message)
        return

    if bot.user.mentioned_in(message):
        user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
        current_model = channel_modes.get(message.channel.id, 'gemini-2.5-flash')
        history_str = get_history_text(message.channel.id)

        try:
            # 1. 画像解析モード
            if message.attachments and any(message.attachments[0].filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                await message.channel.send("👀 画像を確認しています...")
                attachment = message.attachments[0]
                img_data = requests.get(attachment.url).content
                img = Image.open(io.BytesIO(img_data))
                instructions = f"あなたは優秀なAI秘書です。以下の【これまでの会話履歴】を踏まえ、添付画像を見てユーザーの指示に具体的かつ現実的に答えてください。\n{history_str}\n指示: {user_text if user_text else '詳細に説明して'}"
                response = client.models.generate_content(model=current_model, contents=[img, instructions])
                await send_response(message.channel, response.text)
                if user_text: add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                return

            # 2. PDF書類分析モード
            if message.attachments and message.attachments[0].filename.lower().endswith('.pdf'):
                await message.channel.send("📄 PDFファイルを高度解析しています...")
                attachment = message.attachments[0]
                pdf_data = requests.get(attachment.url).content
                pdf_text = ""
                with io.BytesIO(pdf_data) as pdf_file:
                    reader = pypdf.PdfReader(pdf_file)
                    for page in reader.pages:
                        if page.extract_text(): pdf_text += page.extract_text() + "\n"
                prompt = f"あなたはAI秘書です。事実情報のみに基づき具体的かつ現実的に答えてください。\n{history_str}\n【PDF内容】\n{pdf_text[:30000]}\n指示: {user_text if user_text else '要約して'}"
                response = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, response.text)
                if user_text: add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                return

            if not user_text:
                await message.channel.send("はい、何でしょうか？")
                return

            await message.channel.send("🤔 思考中...")
            
            # 検索判定
            intent_check = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"以下の文章が事実確認のウェブ検索が必要な質問か判定し、必要ならYES、不要ならNOとだけ答えてください。\n文章：{user_text}"
            )
            
            # 💡【重要】ここで3秒間待機し、Googleからの制限（429エラー）を回避する！
            await asyncio.sleep(3)
            
            # 3. WEB検索実行
            if "YES" in intent_check.text.upper():
                await message.channel.send("🔍 外部リサーチAIで事実確認を行っています...")
                url = "https://api.tavily.com/search"
                payload = {"api_key": TAVILY_API_KEY, "query": user_text, "search_depth": "advanced", "max_results": 5, "include_raw_content": False}
                search_data = requests.post(url, json=payload).json()
                
                context = "【読み込んだウェブサイトの情報】\n\n"
                if "results" in search_data:
                    for index, result in enumerate(search_data["results"]):
                        context += f"--- 情報源 {index+1}: {result.get('title')} ---\nURL: {result.get('url')}\n内容: {result.get('content')}\n\n"

                prompt = f"あなたはAIリサーチャーです。事実情報のみに基づき正確に答えてください。情報源も記載してください。\n{history_str}\n{context}\n質問: {user_text}"
                await message.channel.send("🧠 情報の分析・統合中...")
                answer = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, answer.text)
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", answer.text)
                
            # 4. 通常の会話
            else:
                prompt = f"あなたはAI秘書です。【これまでの会話履歴】を踏まえて具体的かつ現実的に答えてください。\n{history_str}\n質問：{user_text}"
                response = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, response.text)
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)

        except Exception as e:
            await message.channel.send(f"エラーが発生しました：{str(e)[:1000]}... (省略)")

bot.run(DISCORD_TOKEN)
