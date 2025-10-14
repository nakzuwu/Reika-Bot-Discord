import discord
import os, random, json, datetime
import requests
import re
import aiohttp
from discord.ext import commands
import yt_dlp as youtube_dl
from datetime import datetime,  timedelta, timezone
from discord import File
import asyncio
from collections import deque
from config import BOT_TOKEN, PREFIX
import time
import moviepy as mp
from PIL import Image

# Suppress noise
# youtube_dl.utils.bug_reports_message = lambda: ''

# ============================
# CONFIGURATION & CONSTANTS
# ============================

# YouTube DL Configuration
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

# Paths
DOWNLOADS_PATH = "downloads"
os.makedirs(DOWNLOADS_PATH, exist_ok=True)

# FFmpeg Options
ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# Global instances
ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# ============================
# DATA MODELS
# ============================

class Song:
    __slots__ = ('title', 'url', 'duration', 'thumbnail', 'requester')
    
    def __init__(self, data, requester):
        self.title = data.get('title', 'Unknown Title')
        self.url = data.get('url') or data.get('webpage_url')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail')
        self.requester = requester

    def format_duration(self):
        minutes, seconds = divmod(self.duration, 60)
        return f"{minutes}:{seconds:02d}"

class MusicPlayer:
    def __init__(self):
        self.queue = deque()
        self.loop = False
        self.loop_queue = False
        self.current_song = None
        self.volume = 0.5
        self.playlist_mode = False 
    
    def remove(self, index: int):
        if 1 <= index <= len(self.queue):
            return self.queue.remove(self.queue[index-1])
        return None
    
    def clear(self):
        self.queue.clear()
    
    def shuffle(self):
        import random
        random.shuffle(self.queue)
    
    def move(self, from_pos: int, to_pos: int):
        if 1 <= from_pos <= len(self.queue) and 1 <= to_pos <= len(self.queue):
            song = self.queue[from_pos-1]
            del self.queue[from_pos-1]
            self.queue.insert(to_pos-1, song)
            return song
        return None

# ============================
# BOT SETUP
# ============================

player = MusicPlayer()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True 
)

# ============================
# MUSIC CORE FUNCTIONS
# ============================

def after_playing(error):
    if error:
        print(f'Player error: {error}')
    
    if not bot.voice_clients:
        return
    
    voice_client = bot.voice_clients[0]
    
    if player.loop and player.current_song:
        coro = play_song(voice_client, player.current_song)
    elif player.loop_queue and player.queue and player.current_song:
        player.queue.append(player.current_song)
        next_song = player.queue.popleft()
        coro = play_song(voice_client, next_song)
    elif player.queue:
        next_song = player.queue.popleft()
        coro = play_song(voice_client, next_song)
    else:
        player.current_song = None
        coro = voice_client.disconnect()
    
    asyncio.run_coroutine_threadsafe(coro, bot.loop)

async def play_song(voice_client, song):
    player.current_song = song
    try:
        data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(song.url, download=False))
        filename = data['url']
        source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
        source = discord.PCMVolumeTransformer(source, volume=player.volume)
        voice_client.play(source, after=after_playing)
    except Exception as e:
        print(f'Error playing song: {e}')
        if voice_client.is_connected():
            voice_client.stop()

async def play_next(ctx):
    """Play the next song in queue"""
    if player.queue:
        next_song = player.queue.popleft()
        await play_song(ctx.voice_client, next_song)
        embed = discord.Embed(
            description=f"🎶 Now playing: [{next_song.title}]({next_song.url})",
            color=0x00ff00
        )
        embed.set_footer(text=f"Requested by {next_song.requester.display_name}")
        if next_song.thumbnail:
            embed.set_thumbnail(url=next_song.thumbnail)
        await ctx.send(embed=embed)

async def process_playlist(url, requester):
    """Extract all songs from a playlist"""
    ytdl_playlist = youtube_dl.YoutubeDL({
        **ytdl_format_options,
        'extract_flat': 'in_playlist',
        'noplaylist': False
    })
    
    try:
        data = await bot.loop.run_in_executor(None, ytdl_playlist.extract_info(url, download=False))
        if not data or 'entries' not in data:
            return None
        
        return [Song(entry, requester) for entry in data['entries'] if entry]
    except Exception as e:
        print(f"Playlist error: {e}")
        return None

# ============================
# MEDIA DOWNLOAD FUNCTIONS
# ============================
async def download_media(ctx, url, mode):
    await ctx.send("⏳ Sedang memproses permintaanmu...")

    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOADS_PATH, 'temp_download.%(ext)s'),
        'quiet': True,
        'noplaylist': True,
        'no_warnings': True,
    }

    # Format untuk setiap mode - support YouTube Music
    if mode == 'yt':
        ydl_opts.update({
            'format': 'best[height<=480]/best[ext=mp4]',
            'merge_output_format': 'mp4'
        })
    elif mode == 'ytmp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',  # Increase quality for music
            }]
        })
    elif mode == 'fb':
        ydl_opts.update({
            'format': 'best[ext=mp4]/best',
            'merge_output_format': 'mp4'
        })
    elif mode == 'ig':
        ydl_opts.update({
            'format': 'best[ext=mp4]/best', 
            'merge_output_format': 'mp4'
        })
    else:
        return await ctx.send("🚫 Mode tidak dikenal. Gunakan: `yt`, `ytmp3`, `fb`, atau `ig`.")

    try:
        loop = asyncio.get_event_loop()
        ydl = youtube_dl.YoutubeDL(ydl_opts)
        
        # Extract info untuk mendapatkan metadata
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
        
        # Cek jika ini dari YouTube Music
        is_music = 'music.youtube.com' in url.lower() or info.get('extractor') == 'youtube:tab'
        
        # Download file
        await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
        
        # Tentukan nama file berdasarkan mode
        if mode == 'ytmp3':
            filename = os.path.join(DOWNLOADS_PATH, 'temp_download.mp3')
            file_extension = 'mp3'
        else:
            filename = os.path.join(DOWNLOADS_PATH, 'temp_download.mp4')
            file_extension = 'mp4'

        if not os.path.exists(filename):
            return await ctx.send("❌ File hasil unduhan tidak ditemukan.")

        # Siapkan metadata untuk embed
        title = info.get('title', 'Unknown Title')
        webpage_url = info.get('webpage_url', url)
        thumbnail = info.get('thumbnail')
        
        # Untuk YouTube Music, tambahkan info artist jika ada
        description = f"**[{title}]({webpage_url})**"
        if is_music and info.get('artist'):
            description = f"**🎵 {title}**\n👤 **Artist:** {info.get('artist')}\n🔗 {webpage_url}"
        
        # Kirim hasilnya
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        file = discord.File(filename, filename=f"{safe_title[:50]}.{file_extension}")
        
        embed_title = "✅ Audio berhasil diunduh!" if mode == 'ytmp3' else "✅ Video berhasil diunduh!"
        if is_music:
            embed_title = "🎵 Musik berhasil diunduh!" if mode == 'ytmp3' else "🎵 Video musik berhasil diunduh!"
        
        embed = discord.Embed(
            title=embed_title,
            description=description,
            color=0x00ff00
        )
        
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
            
        # Tambahkan info durasi untuk musik
        if is_music and info.get('duration'):
            duration = info.get('duration')
            minutes, seconds = divmod(duration, 60)
            embed.add_field(name="⏱️ Durasi", value=f"{minutes}:{seconds:02d}", inline=True)
            
        if is_music and info.get('album'):
            embed.add_field(name="💿 Album", value=info.get('album'), inline=True)

        await ctx.send(embed=embed, file=file)

    except Exception as e:
        await ctx.send(f"❌ Terjadi error: `{e}`")

    finally:
        # Bersihkan file
        try:
            if 'filename' in locals() and os.path.exists(filename):
                os.remove(filename)
        except:
            pass
# ============================
# WAIFU SYSTEM FUNCTIONS
# ============================

CLAIM_FILE = "claims.json"
WAIFU_FOLDER = "images/waifu"

# Pastikan folder dan file data ada
os.makedirs(WAIFU_FOLDER, exist_ok=True)
if not os.path.exists(CLAIM_FILE):
    with open(CLAIM_FILE, "w") as f:
        json.dump({}, f, indent=4)

async def handle_waifu_claim(ctx):
    waifu_folder = "./images/waifu"
    claim_file = "claimed_waifus.json"

    if not os.path.exists(waifu_folder):
        await ctx.send("📁 Folder waifu tidak ditemukan!")
        return

    if not os.path.exists(claim_file):
        with open(claim_file, "w") as f:
            json.dump({}, f)

    # Load data lama supaya tidak hilang
    try:
        with open(claim_file, "r") as f:
            content = f.read().strip()
            data = json.loads(content) if content else {}
    except json.JSONDecodeError:
        data = {}

    user_id = str(ctx.author.id)
    today = datetime.now().strftime("%Y-%m-%d")

    # Cek claim hari ini
    if user_id in data and data[user_id].get("date") == today:
        waifu_name = data[user_id]["waifu"]
        await ctx.send(f"💤 Kamu sudah claim hari ini, bebebmu tetap **{waifu_name}**~ 💕")
        return

    waifus = [f for f in os.listdir(waifu_folder) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    if not waifus:
        await ctx.send("⚠️ Tidak ada gambar waifu di folder.")
        return

    chosen = random.choice(waifus)
    waifu_name = os.path.splitext(chosen)[0].replace("_", " ").title()

    # Simpan tanpa hapus data lain
    old_data = data.get(user_id, {})
    old_count = old_data.get("count", 0)

    data[user_id] = {
        "date": today,
        "waifu": waifu_name,
        "count": old_count + 1
    }

    with open(claim_file, "w") as f:
        json.dump(data, f, indent=4)

    await ctx.send(f"💘 Hari ini bebebmu adalah **{waifu_name}**! 💞")

    try:
        await ctx.send(file=File(os.path.join(waifu_folder, chosen)))
    except discord.HTTPException:
        await ctx.send(f"⚠️ Gambar **{waifu_name}** terlalu besar untuk dikirim.")

async def get_top_karbit(ctx):
    claim_file = "claimed_waifus.json"

    if not os.path.exists(claim_file):
        await ctx.send("📂 Belum ada data claim.")
        return

    # Load data
    try:
        with open(claim_file, "r") as f:
            content = f.read().strip()
            data = json.loads(content) if content else {}
    except json.JSONDecodeError:
        data = {}

    if not data:
        await ctx.send("📭 Belum ada yang claim waifu.")
        return

    # Hitung leaderboard berdasarkan count
    leaderboard = []
    for user_id, info in data.items():
        count = info.get("count", 0)
        leaderboard.append((user_id, count))

    leaderboard.sort(key=lambda x: x[1], reverse=True)

    desc = ""
    for i, (user_id, count) in enumerate(leaderboard[:10], start=1):
        user = await ctx.bot.fetch_user(int(user_id))
        desc += f"**{i}.** {user.name} — ❤️ {count}x claim\n"

    embed = discord.Embed(
        title="🏆 Top Karbit Leaderboard",
        description=desc or "Belum ada yang claim 😴",
        color=discord.Color.pink()
    )

    await ctx.send(embed=embed)

# ============================
# BOT EVENTS
# ============================

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="n.help"))

@bot.event
async def on_voice_state_update(member, before, after):
    # Auto-disconnect if alone in voice channel
    if member == bot.user and not after.channel:
        player.clear()
        player.current_song = None
    
    if member != bot.user and before.channel and not after.channel:
        voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)
        if voice_client and voice_client.channel == before.channel:
            if len(voice_client.channel.members) == 1:  # Only bot remains
                await voice_client.disconnect()
                player.clear()
                player.current_song = None

# ============================
# MUSIC COMMANDS
# ============================

@bot.command(aliases=['p'])
async def play(ctx, *, query):
    """Play a song or add to queue - supports playlists"""
    if not ctx.author.voice:
        return await ctx.send("🚫 You need to be in a voice channel!")

    # Bersihkan query
    clean_query = query.strip()
    if not clean_query:
        return await ctx.send("🚫 Please provide a song name or URL")

    # Pastikan bot langsung join voice channel dulu
    voice_client = ctx.voice_client
    if not voice_client:
        try:
            await ctx.author.voice.channel.connect()
            await ctx.send("✅ Connected to voice channel! 🎶")
        except Exception as e:
            return await ctx.send(f"❌ Failed to connect to voice channel: {e}")

    # Kirim pesan status agar user tahu sedang mencari lagu
    status_msg = await ctx.send("🎧 Searching for the song, please wait...")

    async with ctx.typing():
        try:
            # Cek apakah playlist
            if 'list=' in clean_query.lower() and ('youtube.com' in clean_query.lower() or 'youtu.be' in clean_query.lower()):
                try:
                    ytdl_playlist = youtube_dl.YoutubeDL({
                        **ytdl_format_options,
                        'extract_flat': True,
                        'noplaylist': False
                    })

                    playlist_data = await bot.loop.run_in_executor(
                        None,
                        lambda: ytdl_playlist.extract_info(clean_query, download=False)
                    )

                    if not playlist_data or 'entries' not in playlist_data:
                        await status_msg.edit(content="❌ Couldn't process that playlist or playlist is empty")
                        return

                    songs = []
                    for entry in playlist_data['entries']:
                        if entry:
                            songs.append(Song(entry, ctx.author))
                            if len(songs) >= 100:
                                break

                    if not songs:
                        await status_msg.edit(content="❌ No valid songs found in playlist")
                        return

                    # Tambah ke queue
                    player.playlist_mode = True
                    for song in songs:
                        player.queue.append(song)
                    player.playlist_mode = False

                    # Mainkan jika belum main
                    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                        await play_next(ctx)

                    await status_msg.edit(content=f"🎵 Added {len(songs)} songs from playlist: **{playlist_data['title']}**")

                except Exception as e:
                    await status_msg.edit(content=f"❌ Playlist error: {str(e)}")

            # Kalau bukan playlist → lagu tunggal
            else:
                try:
                    with youtube_dl.YoutubeDL(ytdl_format_options) as ytdl_instance:
                        if clean_query.startswith(('http://', 'https://')):
                            data = await bot.loop.run_in_executor(
                                None,
                                lambda: ytdl_instance.extract_info(clean_query, download=False)
                            )
                        else:
                            data = await bot.loop.run_in_executor(
                                None,
                                lambda: ytdl_instance.extract_info(f"ytsearch:{clean_query}", download=False)
                            )

                        if not data:
                            await status_msg.edit(content="❌ No results found")
                            return

                        if 'entries' in data:
                            if not data['entries']:
                                await status_msg.edit(content="❌ No results found")
                                return
                            data = data['entries'][0]

                        song = Song(data, ctx.author)

                        # Tambahkan ke antrian atau mainkan langsung
                        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                            player.queue.append(song)
                            embed = discord.Embed(
                                description=f"🎵 Added to queue: [{song.title}]({song.url})",
                                color=0x00ff00
                            )
                            embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                            await status_msg.edit(content=None, embed=embed)
                        else:
                            await play_song(ctx.voice_client, song)
                            embed = discord.Embed(
                                description=f"🎶 Now playing: [{song.title}]({song.url})",
                                color=0x00ff00
                            )
                            embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                            if song.thumbnail:
                                embed.set_thumbnail(url=song.thumbnail)
                            await status_msg.edit(content=None, embed=embed)

                except Exception as e:
                    await status_msg.edit(content=f"❌ Error processing song: {str(e)}")

        except Exception as e:
            await status_msg.edit(content=f"❌ Unexpected error: {str(e)}")

@bot.command(aliases=['q'])
async def queue(ctx, page: int = 1):
    """Show current queue"""
    if not player.queue and not player.current_song:
        return await ctx.send("ℹ️ The queue is empty!")

    items_per_page = 5  # Reduced from 10 to be safer
    pages = max(1, (len(player.queue) + items_per_page - 1) // items_per_page)
    page = max(1, min(page, pages))

    embed = discord.Embed(title="🎧 Music Queue", color=0x00ff00)
    
    # Current playing song
    if player.current_song:
        current_song_text = f"[{player.current_song.title}]({player.current_song.url})"
        if len(current_song_text) > 256:  # Truncate if too long
            current_song_text = f"{player.current_song.title[:200]}... (click for full)"
        
        embed.add_field(
            name="Now Playing",
            value=f"{current_song_text}\n"
                  f"⏳ {player.current_song.format_duration()} | "
                  f"Requested by {player.current_song.requester.mention}",
            inline=False
        )

    # Queue items
    if player.queue:
        start = (page - 1) * items_per_page
        end = start + items_per_page
        
        queue_list = []
        for i, song in enumerate(list(player.queue)[start:end], start=start+1):
            song_text = f"[{song.title}]({song.url})"
            if len(song_text) > 100:  # Truncate long titles
                song_text = f"{song.title[:80]}... (click for full)"
            
            queue_item = (
                f"`{i}.` {song_text}\n"
                f"⏳ {song.format_duration()} | "
                f"Requested by {song.requester.mention}"
            )
            
            # Ensure each item doesn't exceed 200 chars
            queue_list.append(queue_item[:200])

        # Split into chunks if needed
        queue_text = "\n\n".join(queue_list)
        if len(queue_text) > 1024:
            queue_text = queue_text[:1000] + "\n... (queue too long to display fully)"

        embed.add_field(
            name=f"Up Next (Page {page}/{pages})",
            value=queue_text or "No songs in queue",
            inline=False
        )

    # Loop status
    status = []
    if player.loop:
        status.append("🔂 Single Loop")
    if player.loop_queue:
        status.append("🔁 Queue Loop")
    
    if status:
        embed.set_footer(text=" | ".join(status))

    try:
        await ctx.send(embed=embed)
    except discord.HTTPException as e:
        # Fallback if embed is still too large
        simple_msg = f"Now Playing: {player.current_song.title if player.current_song else 'Nothing'}\n"
        simple_msg += f"Queue: {len(player.queue)} songs (use n.queue with page number to view)"
        await ctx.send(simple_msg)

@bot.command(aliases=['s'])
async def skip(ctx):
    """Skip current song"""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        return await ctx.send("ℹ️ Nothing is currently playing!")
    
    ctx.voice_client.stop()
    await ctx.message.add_reaction("⏭️")

@bot.command()
async def loop(ctx):
    """Toggle loop for current song"""
    player.loop = not player.loop
    player.loop_queue = False if player.loop else player.loop_queue
    await ctx.message.add_reaction("🔂" if player.loop else "➡️")

@bot.command()
async def loopqueue(ctx):
    """Toggle queue looping"""
    player.loop_queue = not player.loop_queue
    player.loop = False if player.loop_queue else player.loop
    await ctx.message.add_reaction("🔁" if player.loop_queue else "➡️")

@bot.command(aliases=['rm'])
async def remove(ctx, index: int):
    """Remove a song from queue"""
    if not player.queue:
        return await ctx.send("ℹ️ The queue is empty!")
    
    if index < 1 or index > len(player.queue):
        return await ctx.send(f"🚫 Please provide a valid position (1-{len(player.queue)})")
    
    removed = player.remove(index)
    embed = discord.Embed(
        description=f"🗑️ Removed: [{removed.title}]({removed.url})",
        color=0x00ff00
    )
    embed.set_footer(text=f"Was position {index} | Requested by {removed.requester.display_name}")
    await ctx.send(embed=embed)

@bot.command(aliases=['c'])
async def clear(ctx):
    """Clear the queue"""
    if not player.queue:
        return await ctx.send("ℹ️ The queue is already empty!")
    
    player.clear()
    await ctx.message.add_reaction("🧹")

@bot.command(aliases=['vol'])
async def volume(ctx, volume: int = None):
    """Set volume (0-100)"""
    if volume is None:
        return await ctx.send(f"🔊 Current volume: {int(player.volume * 100)}%")
    
    if volume < 0 or volume > 100:
        return await ctx.send("🚫 Volume must be between 0 and 100")
    
    player.volume = volume / 100
    if ctx.voice_client and ctx.voice_client.source:
        ctx.voice_client.source.volume = player.volume
    
    await ctx.message.add_reaction("🔊")

@bot.command()
async def shuffle(ctx):
    """Shuffle the queue"""
    if len(player.queue) < 2:
        return await ctx.send("ℹ️ Need at least 2 songs in queue to shuffle!")
    
    player.shuffle()
    await ctx.message.add_reaction("🔀")

@bot.command(aliases=['mv'])
async def move(ctx, from_pos: int, to_pos: int):
    """Move song in queue"""
    if len(player.queue) < 2:
        return await ctx.send("ℹ️ Need at least 2 songs in queue to move!")
    
    moved = player.move(from_pos, to_pos)
    if not moved:
        return await ctx.send(f"🚫 Invalid positions (1-{len(player.queue)})")
    
    embed = discord.Embed(
        description=f"↕️ Moved [{moved.title}]({moved.url}) from position {from_pos} to {to_pos}",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@bot.command(aliases=['h', 'commands'])
async def help(ctx, category: str = None):
    """Show all commands organized by categories"""
    
    # Define command categories
    categories = {
        "music": {
            "name": "🎵 Music Commands",
            "description": "Commands for music playback and queue management",
            "emoji": "🎵"
        },
        "download": {
            "name": "📥 Download Commands", 
            "description": "Commands for downloading media from various platforms",
            "emoji": "📥"
        },
        "waifu": {
            "name": "💖 Waifu System",
            "description": "Waifu claiming and management commands",
            "emoji": "💖"
        },
        "utility": {
            "name": "🔧 Utility Commands",
            "description": "Various utility and fun commands",
            "emoji": "🔧"
        }
    }

    # Auto-categorize commands
    command_categories = {}
    
    for cmd in bot.commands:
        # Skip the help command itself
        if cmd.name == 'help':
            continue
            
        # Categorize based on command name and function
        if any(keyword in cmd.name for keyword in ['play', 'skip', 'queue', 'loop', 'volume', 'shuffle', 'move', 'stop', 'clear', 'remove']):
            category_name = "music"
        elif any(keyword in cmd.name for keyword in ['ytmp3','yt', 'fb', 'ig', 'twitter', 'download', 'thumbnail', 'togif']):
            category_name = "download"
        elif any(keyword in cmd.name for keyword in ['claim', 'waifu', 'karbit', 'resetclaim']):
            category_name = "waifu"
        else:
            category_name = "utility"
            
        if category_name not in command_categories:
            command_categories[category_name] = []
        
        # Get command info
        aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
        command_info = {
            "name": f"{ctx.prefix}{cmd.name}{aliases}",
            "description": cmd.help or "No description available",
            "command": cmd
        }
        command_categories[category_name].append(command_info)

    # If specific category is requested
    if category and category.lower() in categories:
        cat_key = category.lower()
        cat_info = categories[cat_key]
        
        embed = discord.Embed(
            title=f"{cat_info['emoji']} {cat_info['name']}",
            description=cat_info['description'],
            color=0x00ff00
        )
        
        if cat_key in command_categories:
            for cmd_info in sorted(command_categories[cat_key], key=lambda x: x['name']):
                embed.add_field(
                    name=cmd_info['name'],
                    value=cmd_info['description'],
                    inline=False
                )
        else:
            embed.add_field(name="No commands", value="No commands in this category.", inline=False)
            
        embed.set_footer(text=f"Use {ctx.prefix}help for all categories")
        await ctx.send(embed=embed)
        return

    # Show main help menu with all categories
    embed = discord.Embed(
        title=f"{ctx.prefix}🤖 Bot Commands Overview",
        description="Use the commands below to interact with the bot. Commands are organized by category for easy navigation.",
        color=0x00ff00
    )

    # Add category overview
    for cat_key, cat_info in categories.items():
        command_count = len(command_categories.get(cat_key, []))
        example_commands = ", ".join([cmd['command'].name for cmd in command_categories.get(cat_key, [])[:3]])
        if command_count > 3:
            example_commands += f"... (+{command_count - 3} more)"
            
        embed.add_field(
            name=f"{cat_info['emoji']} {cat_info['name']} ({command_count} commands)",
            value=f"{cat_info['description']}\n`{ctx.prefix}help {cat_key}` • Examples: {example_commands}",
            inline=False
        )

    # Add usage tips
    embed.add_field(
        name="💡 Usage Tips",
        value=f"• Use `{ctx.prefix}help <category>` to see commands in a specific category\n• Use `{ctx.prefix}help <command>` for detailed command info\n• Most music commands have shorter aliases for convenience",
        inline=False
    )

    await ctx.send(embed=embed)

@help.error
async def help_error(ctx, error):
    """Error handler for help command"""
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ Category not found. Use `help` to see available categories.")

# Auto-generate command descriptions for commands without help text
def setup_command_help():
    """Automatically add help text to commands that don't have it"""
    command_descriptions = {
        # Music commands
        'play': 'Play a song or add to queue - supports YouTube, playlists, and search queries',
        'skip': 'Skip the currently playing song',
        'queue': 'Show the current music queue with pagination',
        'loop': 'Toggle loop for the current song',
        'loopqueue': 'Toggle queue looping',
        'remove': 'Remove a song from the queue by position',
        'clear': 'Clear all songs from the queue',
        'volume': 'Set the playback volume (0-100) or show current volume',
        'shuffle': 'Shuffle the current queue',
        'move': 'Move a song from one position to another in the queue',
        'stop': 'Stop playback and disconnect from voice channel',
        
        # Download commands
        'yt': 'Download video from YouTube (max 480p with audio)',
        'ytmp3': 'Download audio (MP3) from YouTube',
        'fb': 'Download video from Facebook',
        'ig': 'Download video from Instagram',
        'twitter': 'Download video from Twitter/X with auto-upload for large files',
        'ytthumbnail': 'Get YouTube video thumbnail from URL',
        'togif': 'Convert image/video to GIF (reply to a file or attach one)',
        
        # Waifu commands
        'claim': 'Claim your daily waifu - get a random waifu image each day',
        'resetclaim': '[ADMIN] Reset daily claim for a user',
        'topkarbit': 'Show leaderboard of top waifu claimers',
    }
    
    for cmd_name, description in command_descriptions.items():
        cmd = bot.get_command(cmd_name)
        if cmd and not cmd.help:
            cmd.help = description

# Run the setup when bot starts
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="n.help"))
    setup_command_help()  # Setup auto-help descriptions

@bot.command(aliases=['leave', 'disconnect', 'dc'])
async def stop(ctx):
    """Stop playback and disconnect"""
    if not ctx.voice_client:
        return await ctx.send("ℹ️ I'm not in a voice channel!")
    
    player.clear()
    player.current_song = None
    await ctx.voice_client.disconnect()
    await ctx.message.add_reaction("🛑")

# ============================
# MEDIA DOWNLOAD COMMANDS
# ============================

@bot.command()
async def yt(ctx, url: str):
    """Download video dari YouTube (maks 1080p)"""
    await download_media(ctx, url, 'yt')

@bot.command()
async def ytmp3(ctx, url: str):
    """Download audio (MP3) dari YouTube"""
    await download_media(ctx, url, 'ytmp3')

@bot.command()
async def fb(ctx, url: str):
    """Download video dari Facebook"""
    await download_media(ctx, url, 'fb')

@bot.command()
async def ig(ctx, url: str):
    """Download video dari Instagram"""
    await download_media(ctx, url, 'ig')

@bot.command(name="ytthumbnail")
async def ytthumbnail(ctx, url: str = None):
    if url is None:
        await ctx.send("📺 Gunakan command seperti ini:\n`n.ytthumbnail <link_youtube>`")
        return

    # Regex ambil video ID dari URL YouTube
    pattern = r"(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]{11})"
    match = re.search(pattern, url)

    if not match:
        await ctx.send("⚠️ Tidak bisa menemukan ID video YouTube dari link itu.")
        return

    video_id = match.group(1)
    resolutions = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",  # resolusi tertinggi
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",      # fallback
        f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"       # fallback lagi
    ]

    # Coba ambil thumbnail yang tersedia
    thumbnail_url = None
    async with aiohttp.ClientSession() as session:
        for res_url in resolutions:
            async with session.get(res_url) as resp:
                if resp.status == 200:
                    thumbnail_url = res_url
                    break

    if thumbnail_url is None:
        await ctx.send("😔 Tidak bisa menemukan thumbnail untuk video tersebut.")
        return

    # Buat embed cantik
    embed = discord.Embed(
        title="🎬 YouTube Thumbnail",
        description=f"Thumbnail dari: {url}",
        color=discord.Color.red()
    )
    embed.set_image(url=thumbnail_url)
    embed.set_footer(text="Requested by " + ctx.author.name)

    await ctx.send(embed=embed)
    await ctx.send(f"🖼️ **Link download langsung:** {thumbnail_url}")

@bot.command(name="twitter")
async def download_twitter(ctx, url: str):
    """
    Download video dari Twitter (X) menggunakan yt-dlp.
    Jika file terlalu besar (>25MB), akan diupload ke GoFile.io.
    """
    await ctx.send("🐦 Sedang memproses video Twitter...")

    temp_filename = os.path.join(DOWNLOADS_PATH, "twitter_video.mp4")

    # Hapus file lama kalau ada
    if os.path.exists(temp_filename):
        os.remove(temp_filename)

    # Setup opsi yt-dlp
    ydl_opts = {
        "outtmpl": temp_filename,
        "format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }

    try:
        # Download video
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Cek ukuran file
        file_size = os.path.getsize(temp_filename)
        limit_bytes = 25 * 1024 * 1024  # 25 MB

        if file_size <= limit_bytes:
            await ctx.send("✅ Video berhasil diunduh!", file=File(temp_filename))
        else:
            await ctx.send("⚠️ File terlalu besar, sedang diupload ke GoFile.io...")

            # Dapatkan server upload GoFile
            server_info = requests.get("https://api.gofile.io/getServer").json()
            if server_info["status"] != "ok":
                await ctx.send("❌ Gagal ambil server GoFile.io.")
                return

            server = server_info["data"]["server"]

            # Upload file ke GoFile
            with open(temp_filename, "rb") as f:
                response = requests.post(
                    f"https://{server}.gofile.io/uploadFile",
                    files={"file": f}
                ).json()

            if response["status"] == "ok":
                download_link = response["data"]["downloadPage"]
                await ctx.send(f"📦 Video terlalu besar, tapi sudah diupload!\n🔗 {download_link}")
            else:
                await ctx.send("❌ Gagal mengupload video ke GoFile.io.")

    except Exception as e:
        await ctx.send(f"❌ Gagal mendownload video Twitter: `{e}`")

    finally:
        # Bersihkan file sementara
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

@bot.command()
async def togif(ctx):
    """
    Convert image/video menjadi GIF (<=10MB)
    Bisa reply pesan dengan file, atau kirim file langsung.
    """
    await ctx.send("🎞️ Sedang mengubah ke GIF...")

    DOWNLOAD_LIMIT_BYTES = 10 * 1024 * 1024

    # ambil attachment dari reply atau message
    attachment = None
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
    elif ctx.message.reference:
        ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        if ref.attachments:
            attachment = ref.attachments[0]

    if not attachment:
        return await ctx.send("⚠️ Tidak ada file yang ditemukan. Kirim atau reply file gambar/video!")

    filename = os.path.join(DOWNLOADS_PATH, attachment.filename)
    await attachment.save(filename)

    output_path = os.path.splitext(filename)[0] + ".gif"

    try:
        # Cek tipe file
        if attachment.content_type.startswith("image/"):
            # Gambar → GIF
            with Image.open(filename) as img:
                img.save(output_path, format="GIF")
        elif attachment.content_type.startswith("video/"):
            # Video → GIF (gunakan moviepy)
            clip = mp.VideoFileClip(filename)
            clip = clip.subclip(0, min(clip.duration, 10))  # Maks 10 detik agar kecil
            clip = clip.resize(width=480)  # Biar efisien ukuran
            clip.write_gif(output_path, program="ffmpeg", logger=None)
        else:
            return await ctx.send("❌ Format file tidak didukung. Hanya gambar atau video!")

        # Cek ukuran hasil
        if os.path.getsize(output_path) > DOWNLOAD_LIMIT_BYTES:
            return await ctx.send("⚠️ GIF hasilnya terlalu besar (>10MB). Coba file lebih pendek atau resolusi lebih kecil!")

        # Kirim hasil
        file = discord.File(output_path, filename=os.path.basename(output_path))
        await ctx.send("✅ Berhasil dikonversi ke GIF!", file=file)

    except Exception as e:
        await ctx.send(f"❌ Terjadi error saat konversi: `{e}`")

    finally:
        # Bersihkan file
        try:
            for f in [filename, output_path]:
                if os.path.exists(f):
                    os.remove(f)
        except:
            pass

# ============================
# WAIFU SYSTEM COMMANDS
# ============================

@bot.command(name="claim")
async def claim_waifu(ctx):
    await handle_waifu_claim(ctx)

@bot.command(name="resetclaim")
async def reset_claim_user(ctx, member: discord.Member = None):
    ADMIN_ID = 869897744972668948
    claim_file = "claimed_waifus.json"

    if ctx.author.id != ADMIN_ID:
        await ctx.send("🚫 Kamu tidak punya izin untuk menggunakan command ini.")
        return

    if member is None:
        await ctx.send("⚠️ Tag user yang ingin kamu reset, contoh: `n.resetclaim @user`")
        return

    # Cek file data claim
    if not os.path.exists(claim_file):
        await ctx.send("📁 File claim belum ada.")
        return

    # Load data claim
    try:
        with open(claim_file, "r") as f:
            content = f.read().strip()
            data = json.loads(content) if content else {}
    except json.JSONDecodeError:
        data = {}

    user_id = str(member.id)
    if user_id not in data:
        await ctx.send(f"🙃 {member.mention} belum pernah claim waifu.")
        return

    # Hapus tanggal claim agar bisa claim lagi hari ini
    waifu_name = data[user_id]["waifu"]
    data[user_id]["date"] = ""  # Reset hanya tanggal, bukan seluruh data
    data[user_id]["waifu"] = waifu_name
    data[user_id]["count"] = data[user_id].get("count", 0)  # Pastikan field count tetap ada

    with open(claim_file, "w") as f:
        json.dump(data, f, indent=4)

    await ctx.send(f"🔁 Claim harian {member.mention} telah direset. Sekarang dia bisa claim lagi hari ini 💞")

@bot.command(name="topkarbit")
async def top_karbit(ctx):
    await get_top_karbit(ctx)

BOT_BANS_FILE = "bot_bans.json"

# ---------- Helper load/save ----------
def load_bans():
    if not os.path.exists(BOT_BANS_FILE):
        with open(BOT_BANS_FILE, "w") as f:
            json.dump({}, f)
        return {}
    try:
        with open(BOT_BANS_FILE, "r") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except json.JSONDecodeError:
        # jika rusak, reset ke {}
        return {}

def save_bans(data):
    with open(BOT_BANS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def is_timeout_expired(entry):
    """Entry contoh: {'type':'timeout','until':'2025-10-10T12:34:56'}"""
    if not entry:
        return True
    if entry.get("type") != "timeout":
        return False
    until = entry.get("until")
    if not until:
        return True
    try:
        dt = datetime.fromisoformat(until)
        return datetime.utcnow() >= dt
    except Exception:
        return True

def cleanup_expired_timeouts():
    data = load_bans()
    changed = False
    for uid, entry in list(data.items()):
        if entry.get("type") == "timeout" and is_timeout_expired(entry):
            # hapus entry yang timeout-nya sudah lewat
            del data[uid]
            changed = True
    if changed:
        save_bans(data)

# ---------- Global check for commands ----------
@bot.check
async def global_not_banned_check(ctx):
    """
    Dipanggil sebelum command mana pun.
    Return False akan mencegah perintah dieksekusi.
    """
    cleanup_expired_timeouts()
    data = load_bans()
    user_id = str(ctx.author.id)
    entry = data.get(user_id)
    if not entry:
        return True

    # Permanent ban
    if entry.get("type") == "ban":
        # beri respon singkat
        await ctx.send(f"🚫 Maaf {ctx.author.mention}, kamu diblokir dari menggunakan bot ini. Alasan: {entry.get('reason','-')}")
        return False

    # Timeout (sementara)
    if entry.get("type") == "timeout":
        if is_timeout_expired(entry):
            # seharusnya sudah di-cleanup tapi double-check
            del data[user_id]
            save_bans(data)
            return True
        else:
            until = entry.get("until")
            await ctx.send(f"⏳ Maaf {ctx.author.mention}, akses bot dibatasi sampai **{until} UTC**. Alasan: {entry.get('reason','-')}")
            return False

    return True

# ---------- Block auto-reply (on_message) ----------
# Pastikan auto-reply memeriksa bans juga.
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    cleanup_expired_timeouts()
    data = load_bans()
    if str(message.author.id) in data:
        # Jika user dibanned/timeout, jangan balas keyword atau process command.
        # Namun tetap proses commands supaya global check bisa memberi pesan spesifik.
        await bot.process_commands(message)
        return

    content = message.content.lower()
    
    replies = {
        "jawa": "jawa lagi, jawa lagi",
        "my kisah": "karbitnyooo",
        "bukankah ini": "bukan",
        "samsul": "habis bensin",
        "nkj": "you're done lil bro\n\nIP. 92.28.211.23\nN: 43.7462\nW: 12.4893 SS Number: 6979191519182043\nIPv6: fe80:5dcd.:ef69:fb22::d9 \nUPP: Enabled DMZ: 10.112.42\nMAC: 5A:78:3:7E:00\nDNS: 8.8.8.8\nALT DNS: 1.1.1.8.1\nDNS SUFFIX: Dink WAN: 100.236\nGATEWAY: 192.168\nUDP OPEN PORT: 8080.80",
        "dika": "dika anjeng",
        "osu": "yah ada osu, bete gw njing",
        "help me reika": "In case of an investigation by any federal entity or similar, I do not have any involvement with this group or with the people in it, I do not know how I am here, probably added by a third party, I do not support any actions by members of this group.",
        "lala": "Bete njing ada lala",
        "bedwar": "bising bodo aku nak tido",
        "my bebeb": "karbit bgt njeng",
        "reika": "ap sh manggil manggil, nanti bebeb nkj marah lho",
        "saran lagu": "https://youtu.be/wQu64bXbncI?si=ZM4srvzDHEDo6Oqx",
        "kimi thread": "‼Kimi Thread ‼\nThis is going to be a thread on Kimi (also known as SakudaPikora, MrMolvanstress) and his inappropriate behavior with minors. As well as allowing minors into his discord server that is based off of his YouTube channel (which is very sexual in nature). I’m censoring the name of all minors to avoid exposing them to undesirables",
        "nkj karbit": "maaf, nkj tidak karbit",
    }
    for k, v in replies.items():
        if k in content:
            await message.channel.send(v)
            break

    await bot.process_commands(message)


# Ubah permission check sesuai preferensi: pakai has_permissions(manage_guild=True) atau has_role
# Di sini aku gunakan has_permissions(administrator=True) — hanya admin server yang bisa menjalankan.
@bot.command(name="botban")
@commands.has_permissions(administrator=True)
async def bot_ban(ctx, member: discord.Member, *, reason: str = "Tidak disebutkan"):
    data = load_bans()
    uid = str(member.id)
    data[uid] = {
        "type": "ban",
        "by": str(ctx.author.id),
        "reason": reason,
        "set_at": datetime.now(timezone.utc).isoformat()
    }
    save_bans(data)
    await ctx.send(f"🔒 {member.mention} sekarang diblokir dari memakai bot. Alasan: {reason}")

@bot.command(name="botunban")
@commands.has_permissions(administrator=True)
async def bot_unban(ctx, member: discord.User):
    data = load_bans()
    uid = str(member.id)
    if uid not in data:
        await ctx.send(f"ℹ️ {member.mention} tidak ada di daftar blokir.")
        return
    del data[uid]
    save_bans(data)
    await ctx.send(f"✅ {member.mention} berhasil dihapus dari daftar blokir bot.")

@bot.command(name="bottimeout")
@commands.has_permissions(administrator=True)
async def bot_timeout(ctx, member: discord.Member, minutes: int, *, reason: str = "Tidak disebutkan"):
    """
    Contoh: n.bottimeout @user 60 spam
    """
    if minutes <= 0:
        await ctx.send("🚫 Durasi harus lebih dari 0 menit.")
        return

    until_dt = datetime.utcnow() + timedelta(minutes=minutes)
    data = load_bans()
    uid = str(member.id)
    data[uid] = {
        "type": "timeout",
        "by": str(ctx.author.id),
        "reason": reason,
        "set_at": datetime.utcnow().isoformat(),
        "until": until_dt.isoformat()  # UTC
    }
    save_bans(data)
    await ctx.send(f"⏳ {member.mention} dibatasi akses bot sampai **{until_dt.isoformat()} UTC**. Alasan: {reason}")

@bot.command(name="botbanlist")
@commands.has_permissions(administrator=True)
async def bot_ban_list(ctx):
    cleanup_expired_timeouts()
    data = load_bans()
    if not data:
        await ctx.send("📭 Tidak ada user yang diblokir dari bot.")
        return

    lines = []
    for uid, entry in data.items():
        typ = entry.get("type", "unknown")
        reason = entry.get("reason", "-")
        by = entry.get("by", "-")
        if typ == "timeout":
            until = entry.get("until", "-")
            lines.append(f"<@{uid}> — {typ} until {until} UTC — reason: {reason} — by <@{by}>")
        else:
            lines.append(f"<@{uid}> — {typ} — reason: {reason} — by <@{by}>")

    # Kirim embed (atau pesan biasa jika terlalu panjang)
    embed = discord.Embed(title="🔒 Bot Ban List", description="\n".join(lines[:20]))
    await ctx.send(embed=embed)
# ============================
# BOT START
# ============================

bot.run(BOT_TOKEN)