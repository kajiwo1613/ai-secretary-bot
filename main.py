import discord
from discord.ext import commands
from google import genai
import os
import requests
from PIL import Image
import io
import pypdf

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 部屋ごとのAIモードを記憶する辞書
channel_modes = {}

# 🌟 新機能①：部屋ごとの会話履歴を記憶するメモリシステム
channel_histories = {}

def get_history_text(channel_id):
    """過去の会話文脈をプロンプト用に綺麗に整理して取り出す"""
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
    """会話履歴をメモリに追加（直近5往復＝10件まで自動整理して蓄積）"""
    if channel_id not in channel_histories:
        channel_histories[channel_id] = []
    channel_histories[channel_id].append({"role": role, "text": text})
    if len(channel_histories[channel_id]) > 10:
        channel_histories[channel_id].pop(0)

async def send_response(channel, text):
    """🌟 新機能③：文字数を自動判断し、長文なら.txtファイルでスマートに提出"""
    if not text:
        return
    # 1500文字を超える場合はファイル化して送信
    if len(text) > 1500:
        with io.BytesIO(text.encode('utf-8')) as f:
            await channel.send(
                content="📄 回答が長文（1,500文字以上）になったため、確認しやすいようテキストファイルに出力しました。ダウンロードしてご活用ください：",
                file=discord.File(f, filename="ai_secretary_report.txt")
            )
    else:
        # 通常送信
        await channel.send(text)

@bot.event
async def on_ready():
    print(f"{bot.user} 起動成功 - 究極完全体ハイブリッド版")

@bot.command()
async def mode(ctx, level: str):
    if level.lower() == "pro":
        channel_modes[ctx.channel.id] = 'gemini-2.5-pro'
        await ctx.send("🧠 このチャンネルの頭脳を【Gemini 2.5 Pro（高精度モード）】に設定しました。")
    elif level.lower() == "flash":
        channel_modes[ctx.channel.id] = 'gemini-2.5-flash'
        await ctx.send("⚡ このチャンネルの頭脳を【Gemini 2.5 Flash（高速モード）】に設定しました。")

# 🌟 メインシステム：すべての機能を全自動で処理
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
            # 📸 1. 画像解析モード
            if message.attachments and any(message.attachments[0].filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                await message.channel.send("👀 画像を確認しています...")
                attachment = message.attachments[0]
                img_data = requests.get(attachment.url).content
                img = Image.open(io.BytesIO(img_data))
                
                instructions = f"""
                あなたは優秀なAI秘書です。以下の【これまでの会話履歴】を踏まえ、添付された画像を見てユーザーの指示に具体的かつ現実的に答えてください。
                確証のない憶測は完全に排除してください。
                
                {history_str}
                ユーザーの指示: {user_text if user_text else 'この画像について詳しく説明してください。'}
                """
                
                response = client.models.generate_content(model=current_model, contents=[img, instructions])
                await send_response(message.channel, response.text)
                if user_text:
                    add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                return

            # 📄 2. 新機能②：PDF書類分析モード
            if message.attachments and message.attachments[0].filename.lower().endswith('.pdf'):
                await message.channel.send("📄 確証のある情報を得るため、PDFファイルを高度解析しています。少々お待ちください...")
                attachment = message.attachments[0]
                pdf_data = requests.get(attachment.url).content
                
                # PDFからテキストを抽出
                pdf_text = ""
                with io.BytesIO(pdf_data) as pdf_file:
                    reader = pypdf.PdfReader(pdf_file)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text:
                            pdf_text += text + "\n"
                
                prompt = f"""
                あなたは優秀なAI秘書です。添付された【PDFの内容】および【これまでの会話履歴】という確証のある事実情報のみに基づいて、ユーザーの指示に論理的、具体的、かつ現実的に答えてください。
                データにない推測や不確かな情報は絶対に排除してください。
                
                {history_str}
                【添付されたPDFの内容】
                {pdf_text[:30000]}
                
                ユーザーの指示: {user_text if user_text else 'このPDF書類の重要ポイントを分かりやすく要約してください。'}
                """
                
                response = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, response.text)
                if user_text:
                    add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)
                return

            # 🌐 3. 文字のみの場合（自動検索判定 ＆ 通常対話）
            if not user_text:
                await message.channel.send("はい、何でしょうか？")
                return

            await message.channel.send("🤔 思考中...")
            
            # 最新情報が必要かAIに自己判断させる
            intent_check = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"以下の文章が、最新のニュース、リアルタイムの天気、または事実確認のためのウェブ検索が必要な質問か判定してください。必要なら「YES」、不要なら「NO」とだけ答えてください。\n\n文章：{user_text}"
            )
            
            # 【WEB検索実行】
            if "YES" in intent_check.text.upper():
                await message.channel.send("🔍 最新情報・確証データが必要だと判断しました。専用リサーチAIで事実確認を行っています...")
                url = "https://api.tavily.com/search"
                payload = {
                    "api_key": TAVILY_API_KEY,
                    "query": user_text,
                    "search_depth": "advanced",
                    "max_results": 5,
                    "include_raw_content": False
                }
                response = requests.post(url, json=payload)
                search_data = response.json()
                
                context = "【読み込んだウェブサイトの実際の情報（事実データ）】\n\n"
                if "results" in search_data:
                    for index, result in enumerate(search_data["results"]):
                        title = result.get("title", "無題")
                        link = result.get("url", "URLなし")
                        content = result.get("content", "")
                        context += f"--- 情報源 {index+1}: {title} ---\nURL: {link}\n内容: {content}\n\n"

                prompt = f"""
                あなたは優秀なAIリサーチャーです。以下の【読み込んだウェブサイトの実際の情報】および【これまでの会話履歴】という事実情報のみに基づいて、ユーザーの質問に正確に答えてください。
                確証のない憶測の情報は不必要です。具体的、現実的に情報を提示してください。
                どの情報源（タイトルやURL）からその事実が得られたのかも、合わせて記載してください。
                
                {history_str}
                {context}
                【ユーザーの質問】: {user_text}
                """
                
                await message.channel.send("🧠 リサーチ完了。得られた事実を論理的に分析・統合しています...")
                answer = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, answer.text)
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", answer.text)
                
            # 【通常の会話（検索不要）】
            else:
                prompt = f"""
                あなたは優秀なAI秘書です。【これまでの会話履歴】を踏まえて、ユーザーの質問に具体的、現実的に答えてください。
                確証のない不確かな情報は排除してください。
                
                {history_str}
                質問：{user_text}
                """
                response = client.models.generate_content(model=current_model, contents=prompt)
                await send_response(message.channel, response.text)
                add_history(message.channel.id, "user", user_text)
                add_history(message.channel.id, "model", response.text)

        except Exception as e:
            await message.channel.send(f"エラーが発生しました：{str(e)[:1000]}... (省略)")

bot.run(DISCORD_TOKEN)
