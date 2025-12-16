import asyncio
import json
import os
import re

import aiohttp
import discord
from dotenv import load_dotenv

load_dotenv()
# ---------- CONFIG ----------
SAVE_DIR = "pastry_archive_oct_2025"  # rename each time
ASSETS_DIR = os.path.join(SAVE_DIR, "assets")
SERVER_ID = 1385413666350039160  # change this to server id
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
MAX_FILENAME_LENGTH = 150
# ----------------------------

# ---------- .ENV SET UP  ----------
if not BOT_TOKEN:
    raise ValueError("No Discord token available in .env!")
print(f"Loaded token: {BOT_TOKEN[:5]}...")
# ----------------------------


def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name)


os.makedirs(ASSETS_DIR, exist_ok=True)

# ---------- DISCORD CLIENT ----------
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.members = True
client = discord.Client(intents=intents)


# ---------- ATTACHMENTS ----------
async def download_attachment(session, url, filename):
    filename = filename[:MAX_FILENAME_LENGTH]
    save_path = os.path.join(ASSETS_DIR, filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    async with session.get(url) as resp:
        if resp.status == 200:
            with open(save_path, "wb") as f:
                f.write(await resp.read())
            return f"assets/{filename}"
        return url


# ---------- ARCHIVE MESSAGES ----------
async def archive_messages(channel, session):
    json_path = os.path.join(SAVE_DIR, f"{sanitize_filename(channel.name)}.json")
    messages_list = []
    after = None

    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
            messages_list = existing_data.get("messages", [])
            if messages_list:
                last_msg_id = messages_list[-1]["id"]
                after = discord.Object(
                    id=int(last_msg_id)
                )  # if the process is interrupted, resume from last message
                # unironically quite important as before it would have been impossible to do in one sitting for large servers and bad bandwidth
                print(f"Resuming from message ID {last_msg_id}")

    async for msg in channel.history(limit=None, oldest_first=True, after=after):
        attachments_local = []
        tasks = []
        for att in msg.attachments:
            filename = f"{msg.id}_{sanitize_filename(att.filename)}"
            if not os.path.exists(os.path.join(ASSETS_DIR, filename)):
                tasks.append(download_attachment(session, att.url, filename))
        if tasks:
            attachments_local = await asyncio.gather(*tasks)

        messages_list.append(
            {
                "id": str(msg.id),
                "type": str(msg.type),
                "timestamp": str(msg.created_at),
                "timestampEdited": str(msg.edited_at) if msg.edited_at else None,
                "isPinned": msg.pinned,
                "content": msg.content,
                "author": {
                    "id": str(msg.author.id),
                    "name": msg.author.name,
                    "discriminator": msg.author.discriminator,
                    "nickname": msg.author.display_name,
                    "color": "#FFFFFF",
                    "isBot": msg.author.bot,
                    "avatarUrl": str(msg.author.display_avatar.url),
                },
                "attachments": attachments_local,
                "embeds": [str(embed.to_dict()) for embed in msg.embeds],
                "stickers": [sticker.name for sticker in msg.stickers],
                "reactions": [str(reaction.emoji) for reaction in msg.reactions],
                "mentions": [str(user.id) for user in msg.mentions],
            }
        )

        if len(messages_list) % 100 == 0:
            print(f"[{channel.name}] Fetched {len(messages_list)} messages so far...")

    return messages_list


# ---------- CHANNEL ARCHIVE ----------
async def archive_channel(channel, idx, total, session):
    print(f"[{idx}/{total}] Archiving channel: {channel.name}")
    json_path = os.path.join(SAVE_DIR, f"{sanitize_filename(channel.name)}.json")
    channel_data = {"messages": [], "threads": []}

    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            channel_data = json.load(f)

    # --- main messages ---
    new_messages = await archive_messages(channel, session)
    channel_data["messages"] = new_messages

    # --- active and archived threads ---
    all_threads = list(channel.threads) + [
        t async for t in channel.archived_threads(limit=None)
    ]
    for thread in all_threads:
        thread_json_path = os.path.join(
            SAVE_DIR, f"{sanitize_filename(thread.name)}.json"
        )
        thread_data = {"id": str(thread.id), "name": thread.name, "messages": []}
        if os.path.exists(thread_json_path):
            with open(thread_json_path, "r", encoding="utf-8") as f:
                thread_data = json.load(f)

        thread_data["messages"] = await archive_messages(thread, session)
        with open(thread_json_path, "w", encoding="utf-8") as f:
            json.dump(thread_data, f, ensure_ascii=False, indent=2)

        channel_data["threads"] = [
            t for t in channel_data["threads"] if t["id"] != thread_data["id"]
        ]
        channel_data["threads"].append(thread_data)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(channel_data, f, ensure_ascii=False, indent=2)

    print(
        f"[{idx}/{total}] Completed channel: {channel.name} ({len(new_messages)} new messages, {len(channel_data['threads'])} threads)"
    )


# ---------- ON_READY ----------
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    guild = client.get_guild(SERVER_ID)
    if not guild:
        print(f"Server {SERVER_ID} not found!")
        await client.close()
        return

    channels = guild.text_channels
    total_channels = len(channels)

    async with aiohttp.ClientSession() as session:
        for idx, ch in enumerate(channels, start=1):
            await archive_channel(ch, idx, total_channels, session)
            print(f"Overall progress: {idx}/{total_channels} channels archived")

    print("Archival complete!")
    await client.close()


client.run(BOT_TOKEN)
