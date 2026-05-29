import discord
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
channel_roles = {}

def get_history_text(channel_id):
    long_term = long_term_memories.get(channel_id, "")
    history = channel_histories.get(channel_id, [])
    
    text = ""
    if long_term:
        text += f"【重要：これまでの長期記憶・要約】\n{long_term}\n\n"
    if history:
        text += "【直近の会話履歴】\n"
        for h in history:
            role_name = "ユーザー" if h["role"] == "user" else "AI"
            text += f"{role_name}: {h['text']}\n"
        text += "（履歴ここまで。この流れを踏まえて会話してください）\n\n"
    return text

def add_history(channel_id, role, text):
    if channel_id not in channel_histories:
        channel_histories[channel_id] = []
    channel_histories[channel_id].append({"role": role, "text": text})

async def summarize_memory(channel_id):
    history = channel_histories.get(channel_id, [])
    if len(history) >= 8:
        try:
            current_long = long_term_memories.get(channel_id, "")
            hist_text = "\n".join([f"{h['role']}: {h['text']}" for h in history])
            prompt = f"あなたは裏方の記憶整理係です。これまでの【長期記憶】と【直近の会話】を統合し、ユーザーの興味・前提知識・重要な話題を最新の【要約記憶】として箇条書きで更新してください。\n\n【長期記憶】\n{current_long}\n\n【直近の会話】\n{hist_text}"
            
            await asyncio.sleep(3)
            # 💡 最新の 2.5 に修正
            res = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            if res.text:
                long_term_memories[channel_id] = res.text
                channel_histories[channel_id] = channel_histories[channel_id][-4:]
        except Exception:
            pass

async def send_response(channel, text):
    if not text: return
    if len(text) > 1500:
        with io.BytesIO(text.encode('utf-8-sig')) as f:
            await channel.send(
                content="📄 回答が長文になったため、テキストファイルに出力しました：",
                file=discord.File(f, filename="ai_secretary_report.txt")
            )
    else:
        await channel.send(text)

@bot.event
async def on_ready():
    print(f"{bot.user} 起動成功 - 最新版（2.5）モデル完全復旧版")

@bot.command()
async def remind(ctx, minutes: int, *, message: str):
    await ctx.send(f"⏰ 了解しました！{minutes}分後にリマインドします。")
    await asyncio.sleep(minutes * 60)
    await ctx.send(f"{ctx.author.mention} ⏰ お時間です！\n【リマインド内容】: {message}")

@bot.command()
async def role(ctx, *, persona: str):
    if persona == "リセット":
        channel_roles.pop(ctx.channel.id, None)
        await ctx.send("🔄 AIの人格を通常の「優秀なAI秘書」に戻しました。")
    else:
        channel_roles[ctx.channel.id] = persona
        await ctx.send(f"🎭 AIの人格を【{persona}】に設定しました！")

@bot.command()
async def mode(ctx, level: str):
    if level.lower() == "pro":
        # 💡 最新の 2.5 に修正
        channel_modes[ctx.channel.id] = 'gemini-2.5-pro'
        await ctx.send("🧠 頭脳を【Gemini 2.5 Pro（高精度モード）】に設定しました。")
    elif level.lower() == "flash":
        # 💡 最新の 2.5 に修正
        channel_modes[ctx.channel.id] = 'gemini-2.5-flash'
        await ctx.send("⚡ 頭脳を【Gemini 2.5 Flash（高速モード）】に設定しました。")

@bot.event
async def on_message(message):
    if message.author.bot: return

    message.content = message.content.replace('！', '!').replace('　', ' ')

    if message.content.startswith('!'):
        await bot.process_commands(message)
        return

    if bot.user.mentioned_in(message):
        user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
        # 💡 デフォルトも 2.5 に修正
        current_model = channel_modes.get(message.channel.id, 'gemini-2.5-flash')
        current_role = channel_roles.get(message.channel.id, "優秀なAI秘書")
        history_str = get_history_text(message.channel.id)

        url_pattern = re.compile(r'https?://\S+')
        urls = url_pattern.findall(user_text)
        url_content = ""
        if urls:
            await message.channel.send("🌐 リンク先のウェブサイトを直接読み込んでいます...")
            for url in urls[:2]:
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                    res = requests.get(url, headers=headers, timeout=5)
                    soup = BeautifulSoup(res.text, 'html.parser')
                    extracted_text = soup.get_text(separator='\n', strip=True)
                    url_content += f"【URL: {url} の内容】\n{extracted_text[:3000]}\n\n"
                except:
                    url_content += f"【URL: {url} は読み込めませんでした】\n"

        try:
            if message.attachments and any(message.attachments[0].filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                await message.channel.send("👀 画像を確認しています...")
                img_data = requests.get(message.attachments[0].url).content
                img = Image.open(io.BytesIO(img_data))
                instructions = f"あなたは{current_role}です。以下の履歴を踏まえ、添付画像を見て答えてください。\n{history_str}\n{url_content}\n指示: {user_text if user_text else '詳細に説明して'}"
                response = client.models.generate_content(model=current_model, contents=[img, instructions])
                await send_response(message.channel, response.text)
                if user_text: add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                bot.loop.create_task(summarize_memory(message.channel.id))
                return

            if message.attachments and message.attachments[0].filename.lower().endswith('.pdf'):
                await message.channel.send("📄 PDFファイルを高度解析しています...")
                pdf_data = requests.get(message.attachments[0].url).content
                pdf_text = ""
                with io.BytesIO(pdf_data) as pdf_file:
                    reader = pypdf.PdfReader(pdf_file)
                    for page in reader.pages:
                        if page.extract_text(): pdf_text += page.extract_text() + "\n"
                prompt = f"あなたは{current_role}です。事実情報のみに基づき答えてください。\n{history_str}\n{url_content}\n【PDF内容】\n{pdf_text[:30000]}\n指示: {user_text if user_text else '要約して'}"
                response = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, response.text)
                if user_text: add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                bot.loop.create_task(summarize_memory(message.channel.id))
                return

            if message.attachments and any(message.attachments[0].filename.lower().endswith(ext) for ext in ['.mp3', '.wav', '.m4a', '.ogg', '.oga']):
                await message.channel.send("👂 音声データを聴き取っています...")
                audio_data = requests.get(message.attachments[0].url).content
                mime_type = message.attachments[0].content_type if message.attachments[0].content_type else 'audio/mp3'
                audio_part = types.Part.from_bytes(data=audio_data, mime_type=mime_type)
                instructions = f"あなたは{current_role}です。添付された音声を聴き取り、正確に文字起こしした上で答えてください。\n{history_str}\n{url_content}\n指示: {user_text if user_text else '要約して'}"
                response = client.models.generate_content(model=current_model, contents=[audio_part, instructions])
                await send_response(message.channel, response.text)
                if user_text: add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                bot.loop.create_task(summarize_memory(message.channel.id))
                return

            if not user_text and not url_content:
                await message.channel.send("はい、何でしょうか？")
                return

            await message.channel.send("🤔 思考中...")
            
            # 💡 検索判定も 2.5 に修正
            intent_check = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"以下の文章が事実確認のウェブ検索が必要な質問か判定し、必要ならYES、不要ならNOとだけ答えてください。\n文章：{user_text}"
            )
            
            await asyncio.sleep(3)
            
            if "YES" in intent_check.text.upper():
                await message.channel.send("🔍 外部リサーチAIで事実確認を行っています...")
                url = "https://api.tavily.com/search"
                payload = {"api_key": TAVILY_API_KEY, "query": user_text, "search_depth": "advanced", "max_results": 5, "include_raw_content": False}
                search_data = requests.post(url, json=payload).json()
                
                context = "【読み込んだウェブサイトの情報】\n\n"
                if "results" in search_data:
                    for index, result in enumerate(search_data["results"]):
                        context += f"--- 情報源 {index+1}: {result.get('title')} ---\nURL: {result.get('url')}\n内容: {result.get('content')}\n\n"

                prompt = f"あなたは{current_role}です。事実情報のみに基づき正確に答えてください。\n{history_str}\n{url_content}\n{context}\n質問: {user_text}"
                await message.channel.send("🧠 情報の分析・統合中...")
                answer = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, answer.text)
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", answer.text)
                bot.loop.create_task(summarize_memory(message.channel.id))
                
            else:
                prompt = f"あなたは{current_role}です。【これまでの会話履歴】を踏まえて具体的かつ現実的に答えてください。\n{history_str}\n{url_content}\n質問：{user_text}"
                response = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, response.text)
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                bot.loop.create_task(summarize_memory(message.channel.id))

        except Exception as e:
            await message.channel.send(f"エラーが発生しました：{str(e)[:1000]}... (省略)")

bot.run(DISCORD_TOKEN)
