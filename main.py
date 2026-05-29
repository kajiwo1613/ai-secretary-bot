import discord
from discord.ext import commands
from google import genai
import os
import requests
from PIL import Image
import io

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 部屋ごとのAIモードを記憶する辞書（デフォルトは 2.5-flash）
channel_modes = {}

@bot.event
async def on_ready():
    print(f"{bot.user} 起動成功 - 完全自動化ハイブリッド版")

# 🌟 新機能：部屋のAIモードを切り替えるコマンド
@bot.command()
async def mode(ctx, level: str):
    if level.lower() == "pro":
        channel_modes[ctx.channel.id] = 'gemini-2.5-pro'
        await ctx.send("🧠 このチャンネルの頭脳を【Gemini 2.5 Pro（高精度モード）】に設定しました。")
    elif level.lower() == "flash":
        channel_modes[ctx.channel.id] = 'gemini-2.5-flash'
        await ctx.send("⚡ このチャンネルの頭脳を【Gemini 2.5 Flash（高速モード）】に設定しました。")
    else:
        await ctx.send("⚠️ `!mode pro` か `!mode flash` のどちらかを入力してください。")

# 🌟 新機能：メンションで全自動対応（検索判定＆画像読み込み）
@bot.event
async def on_message(message):
    # ボット自身の発言は無視
    if message.author.bot:
        return

    # コマンド（!modeなど）が打たれた場合はそちらを優先
    if message.content.startswith('!'):
        await bot.process_commands(message)
        return

    # ボットがメンション（@AI秘書）された時だけ動く
    if bot.user.mentioned_in(message):
        # メンションの文字（@AI秘書）を消して純粋な質問だけにする
        user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
        if not user_text and not message.attachments:
            await message.channel.send("はい、何でしょうか？")
            return

        # 現在の部屋のモードを取得（設定されていなければFlash）
        current_model = channel_modes.get(message.channel.id, 'gemini-2.5-flash')

        try:
            # 📸 1. 画像が添付されている場合（画像解析モード）
            if message.attachments:
                await message.channel.send("👀 画像を確認しています...")
                attachment = message.attachments[0]
                
                # Discordから画像をダウンロード
                img_data = requests.get(attachment.url).content
                img = Image.open(io.BytesIO(img_data))
                
                # 画像とテキストをGeminiに渡す
                contents = [img, f"あなたは優秀なAI秘書です。\nユーザーからの指示: {user_text}"] if user_text else [img, "この画像について詳しく説明してください。"]
                
                response = client.models.generate_content(
                    model=current_model,
                    contents=contents
                )
                
                # 長文分割送信
                for i in range(0, len(response.text), 1900):
                    await message.channel.send(response.text[i:i+1900])
                return

            # 🔍 2. 文字だけの場合（裏側で「検索が必要か」をAIに自己判断させる）
            await message.channel.send("🤔 質問の意図を分析中...")
            
            # AIに「検索が必要か？」とこっそり聞く
            intent_check = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"以下の文章が、最新のニュース、リアルタイムの天気、または事実確認のためのウェブ検索が必要な質問か判定してください。必要なら「YES」、不要なら「NO」とだけ答えてください。\n\n文章：{user_text}"
            )
            
            # 🌐 検索が必要（YES）と判断した場合
            if "YES" in intent_check.text.upper():
                await message.channel.send("🔍 最新情報が必要だと判断しました。専用の外部リサーチAIで事実確認を行っています...")
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
                
                context = "【読み込んだウェブサイトの実際の情報】\n\n"
                if "results" in search_data:
                    for index, result in enumerate(search_data["results"]):
                        title = result.get("title", "無題")
                        link = result.get("url", "URLなし")
                        content = result.get("content", "")
                        context += f"--- 情報源 {index+1}: {title} ---\nURL: {link}\n内容: {content}\n\n"

                prompt = f"""
                あなたは優秀なAIリサーチャーです。以下の事実情報のみに基づいて、ユーザーの質問に正確に答えてください。
                {context}
                【ユーザーの質問】: {user_text}
                """
                
                await message.channel.send("🧠 リサーチ完了。情報を分析・統合しています...")
                answer = client.models.generate_content(model=current_model, contents=prompt)
                
                if answer.text:
                    for i in range(0, len(answer.text), 1900):
                        await message.channel.send(answer.text[i:i+1900])
                
            # 💬 検索が不要（NO）と判断した場合（普通の会話）
            else:
                response = client.models.generate_content(
                    model=current_model,
                    contents=f"あなたは優秀なAI秘書です。以下の質問に答えてください。\n\n質問：{user_text}"
                )
                for i in range(0, len(response.text), 1900):
                    await message.channel.send(response.text[i:i+1900])

        except Exception as e:
            await message.channel.send(f"エラーが発生しました：{str(e)[:1000]}... (省略)")

bot.run(DISCORD_TOKEN)
