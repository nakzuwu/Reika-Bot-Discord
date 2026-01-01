import discord
import os
import random
import json
import datetime
import requests
import re
import uuid
import traceback
import sys
import gc
import psutil
import threading
from collections import defaultdict
import aiohttp
import asyncio
import time
from pathlib import Path
from discord.ext import commands
from discord import File
from datetime import datetime, timedelta, timezone
from collections import deque
from config import BOT_TOKEN, PREFIX, GENIUS_API_KEY

COOKIES_PATH = Path('cookies.txt')
# Third-party imports dengan error handling
try:
    import yt_dlp as youtube_dl
    YTDL_AVAILABLE = True
except ImportError:
    print("❌ yt-dlp tidak terinstall. Fitur music tidak akan bekerja.")
    YTDL_AVAILABLE = False

try:
    import lyricsgenius
    if GENIUS_API_KEY:
        genius = lyricsgenius.Genius(GENIUS_API_KEY)
        genius.verbose = False
        genius.remove_section_headers = True
        genius.skip_non_songs = True
        print("✅ Genius API configured successfully!")
    else:
        genius = None
        print("⚠️  Genius API key not set. Lyrics feature will be disabled.")
except ImportError:
    genius = None
    print("⚠️  lyricsgenius not installed. Lyrics feature disabled.")

try:
    from PIL import Image, ImageEnhance, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    print("⚠️  PIL/Pillow not installed. GIF conversion disabled.")
    PIL_AVAILABLE = False

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
    'ignoreerrors': True,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    
    # ⭐⭐ NONAKTIFKAN SEMUA FITUR RESUME/CACHE ⭐⭐
    'no_cache_dir': True,
    'cachedir': False,
    'nooverwrites': False,
    'continuedl': False,
    'nopart': True,
    'updatetime': False,
    
    # ⭐⭐ FORCE FRESH DOWNLOAD SETIAP KALI ⭐⭐
    'forceurl': True,
    'forcetitle': True,
    'forceid': True,
    'forcejson': True,
    'forcethumbnail': True,
    'forcedescription': True,
    'forcefilename': True,
    'forceduration': True,
    
    # Hanya gunakan cookies jika file ada
    **({'cookiefile': str(COOKIES_PATH)} if COOKIES_PATH.exists() else {})
}
# Paths
DOWNLOADS_PATH = "downloads"
os.makedirs(DOWNLOADS_PATH, exist_ok=True)

# FFmpeg Options
ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

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
        if self.duration == 0:
            return "Live"
        minutes, seconds = divmod(self.duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


class MusicPlayer:
    def __init__(self):
        self.players = {}
        self.default_volume = 0.5
    
    def get_player(self, guild_id):
        if guild_id not in self.players:
            self.players[guild_id] = {
                'queue': [],
                'current_song': None,
                'volume': self.default_volume,
                'loop': False,
                'loop_queue': False,
                'playlist_mode': False,
                'is_playing': False,  # Track playback state
                'skip_requested': False,  # Track skip requests
            }
        return self.players[guild_id]
    
    def clear_guild(self, guild_id):
        """Clear player for specific guild"""
        if guild_id in self.players:
            self.players[guild_id] = {
                'queue': [],
                'current_song': None,
                'volume': self.default_volume,
                'loop': False,
                'loop_queue': False,
                'playlist_mode': False
            }

player = MusicPlayer()

# ============================
# BOT SETUP
# ============================

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
# HELPER FUNCTIONS
# ============================

def get_guild_player(ctx):
    """Get guild-specific player"""
    return player.get_player(ctx.guild.id)

def get_guild_player_by_id(guild_id):
    """Get player by guild_id directly"""
    return player.get_player(guild_id)

async def get_context_from_guild(guild_id):
    """Create a minimal context from guild_id"""
    guild = bot.get_guild(guild_id)
    if guild and guild.text_channels:
        channel = guild.text_channels[0]
        class SimpleContext:
            def __init__(self, guild, channel):
                self.guild = guild
                self.channel = channel
                self.voice_client = guild.voice_client
        return SimpleContext(guild, channel)
    return None

# ============================
# MUSIC CORE FUNCTIONS
# ============================

def get_ffmpeg_options(force_start_zero=True):
    """Get FFmpeg options yang PASTI mulai dari 0"""
    if force_start_zero:
        return {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss 0 -nostdin',
            'options': '-vn -b:a 128k -af "aresample=async=1:min_hard_comp=0.100:first_pts=0"'
        }
    return {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
        'options': '-vn -b:a 128k'
    }

async def play_song(voice_client, song, ctx=None):
    """Play song dengan PASTI mulai dari detik 0 - FIXED VERSION"""
    try:
        print(f"🎵 Starting FRESH playback for: {song.title}")
        
        # SIMPAN CONTEXT ATAU BUAT CONTEXT BARU
        if ctx is None:
            guild = voice_client.guild
            text_channel = None
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    text_channel = channel
                    break
            
            if text_channel:
                class FakeContext:
                    def __init__(self):
                        self.voice_client = voice_client
                        self.guild = guild
                        self.channel = text_channel
                        self.author = guild.me
                ctx = FakeContext()
        
        guild_id = voice_client.guild.id
        
        import time
        import random
        
        timestamp = int(time.time())
        random_str = random.randint(100000, 999999)
        
        # Config tanpa cache
        fresh_opts = {
            **ytdl_format_options,
            'no_cache_dir': True,
            'cachedir': False,
            'force_generic_extractor': True,
        }
        
        # ====== PERBAIKAN 1: TAMPILKAN STATUS SEARCH ======
        if 'search:' in song.url.lower() or 'ytsearch:' in song.url.lower():
            print("🔍 Processing search query...")
            if ctx and hasattr(ctx, 'channel'):
                try:
                    await ctx.channel.send("🔍 Mencari lagu...")
                except:
                    pass
        
        with youtube_dl.YoutubeDL(fresh_opts) as ydl:
            info = await bot.loop.run_in_executor(
                None,
                lambda: ydl.extract_info(song.url, download=False)
            )
            
            if not info or 'url' not in info:
                raise Exception("No audio URL found")
            
            url = info['url']
            
            # Tambahkan parameter anti-cache
            if '?' in url:
                url = f"{url}&_nocache={timestamp}{random_str}&_start=0"
            else:
                url = f"{url}?_nocache={timestamp}{random_str}&_start=0"
            
            print(f"🔗 Fresh URL: {url[:80]}...")
        
        # ====== PERBAIKAN 2: CEK JIKA URL VALID ======
        if not url or url.strip() == '':
            raise Exception("Invalid audio URL obtained")
        
        # FFMPEG OPTIONS dengan timeout yang lebih baik
        ffmpeg_opts = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss 0 -nostdin -timeout 60000000',
            'options': '-vn -b:a 128k -bufsize 1024k'
        }
        
        # Stop playback yang ada
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
            await asyncio.sleep(0.5)
        
        # ====== PERBAIKAN 3: FLAG UNTUK TRACK PLAYBACK STATE ======
        playback_started = False
        playback_error = None
        
        # Buat audio source dengan timeout
        try:
            source = await discord.FFmpegOpusAudio.from_probe(
                url,
                **ffmpeg_opts,
                timeout=30  # Timeout 30 detik
            )
        except Exception as e:
            print(f"⚠️ FFmpegOpusAudio probe failed: {e}")
            try:
                source = discord.FFmpegPCMAudio(
                    url,
                    **ffmpeg_opts
                )
            except Exception as e2:
                print(f"❌ FFmpegPCMAudio failed: {e2}")
                raise Exception(f"Gagal membuat audio source: {e2}")
        
        # ====== PERBAIKAN 4: CALLBACK YANG LEBIH AMAN ======
        def after_playing(error):
            """Callback setelah lagu selesai"""
            nonlocal playback_started, playback_error
            
            print(f"🔔 After playing callback triggered. Error: {error}")
            
            # Jika belum pernah mulai playback, ini adalah error awal
            if not playback_started:
                print("⚠️ Callback triggered before playback started!")
                playback_error = error
                return  # JANGAN panggil play_next jika belum mulai!
            
            # Langsung panggil play_next untuk guild ini
            if error:
                print(f"⚠️ Playback error: {error}")
            
            # Gunakan asyncio.run_coroutine_threadsafe untuk bot loop
            async def play_next_wrapper():
                try:
                    print(f"🔄 Callback: Attempting to play next for guild {guild_id}")
                    
                    # TUNGGU 2 DETIK untuk memastikan state konsisten
                    await asyncio.sleep(2)
                    
                    # Cek jika voice client masih connected
                    voice_client_found = None
                    for vc in bot.voice_clients:
                        if vc.guild.id == guild_id and vc.is_connected():
                            voice_client_found = vc
                            break
                    
                    if not voice_client_found:
                        print("❌ Voice client not found or disconnected")
                        return
                    
                    # Cari text channel
                    guild = voice_client_found.guild
                    text_channel = None
                    for channel in guild.text_channels:
                        if channel.permissions_for(guild.me).send_messages:
                            text_channel = channel
                            break
                    
                    if text_channel:
                        class FakeContext:
                            def __init__(self):
                                self.voice_client = voice_client_found
                                self.guild = guild
                                self.channel = text_channel
                                self.author = guild.me
                        
                        fake_ctx = FakeContext()
                        
                        # TUNGGU LAGI untuk pastikan
                        await asyncio.sleep(0.5)
                        
                        await play_next(fake_ctx)
                        print(f"✅ Callback: Successfully triggered play_next")
                    
                except Exception as e:
                    print(f"❌ Error in play_next_wrapper: {e}")
                    import traceback
                    traceback.print_exc()
            
            asyncio.run_coroutine_threadsafe(play_next_wrapper(), bot.loop)
        
        # ====== PERBAIKAN 5: TRY-CATCH SAAT MEMULAI PLAYBACK ======
        try:
            # Set flag bahwa kita akan mulai playback
            playback_started = True
            
            # Play dengan callback
            voice_client.play(source, after=after_playing)
            print(f"✅ Playing FRESH: {song.title}")
            
            # Update current song di guild player
            guild_player = get_guild_player_by_id(guild_id)
            if guild_player:
                guild_player['current_song'] = song
                guild_player['is_playing'] = True  # Tambah flag playing
            
            # ====== PERBAIKAN 6: VERIFIKASI PLAYBACK BENAR-BENAR MULAI ======
            await asyncio.sleep(1)  # Tunggu 1 detik
            
            if not voice_client.is_playing():
                print("⚠️ Playback didn't start after 1 second!")
                if playback_error:
                    raise Exception(f"Playback failed to start: {playback_error}")
                else:
                    # Coba stop dan start ulang
                    voice_client.stop()
                    await asyncio.sleep(0.5)
                    
                    # Coba buat source baru
                    try:
                        source2 = discord.FFmpegPCMAudio(url, **ffmpeg_opts)
                        voice_client.play(source2, after=after_playing)
                        print("🔄 Restarted playback with PCM")
                    except Exception as restart_error:
                        raise Exception(f"Failed to restart: {restart_error}")
            
            return True
            
        except Exception as play_error:
            print(f"❌ Error starting playback: {play_error}")
            playback_started = False
            raise play_error
        
    except Exception as e:
        print(f"❌ Error in play_song: {e}")
        import traceback
        traceback.print_exc()
        
        # ====== PERBAIKAN 7: KIRIM PESAN ERROR KE CHANNEL ======
        if ctx and hasattr(ctx, 'channel'):
            try:
                error_msg = str(e)
                if "search" in song.url.lower() and ("No audio URL" in error_msg or "No video found" in error_msg):
                    await ctx.channel.send("❌ Tidak dapat menemukan lagu. Coba kata kunci yang lebih spesifik.")
                else:
                    await ctx.channel.send(f"❌ Error: {error_msg[:100]}")
            except:
                pass
        
        raise e
    
# async def play_next(ctx=None, guild_id=None):
#     """Play next song in queue - FIXED VERSION"""
#     try:
#         print("🎵 PLAY_NEXT called")
        
#         # Dapatkan guild_id
#         if guild_id is None:
#             if ctx is None:
#                 print("❌ No context or guild_id provided")
#                 return
#             guild_id = ctx.guild.id
#             voice_client = ctx.voice_client
#         else:
#             # Cari voice client berdasarkan guild_id
#             voice_client = None
#             for vc in bot.voice_clients:
#                 if vc.guild.id == guild_id:
#                     voice_client = vc
#                     break
            
#             if not voice_client:
#                 print(f"❌ No voice client for guild {guild_id}")
#                 return
        
#         # Cek koneksi voice
#         if not voice_client or not voice_client.is_connected():
#             print(f"❌ Voice client not connected for guild {guild_id}")
#             return
        
#         # Dapatkan guild player
#         guild_player = guild_players.get(guild_id)
#         if not guild_player:
#             print(f"❌ No guild player for guild {guild_id}")
#             return
        
#         print(f"🎵 PLAY_NEXT - Guild: {guild_id}")
#         print(f"🎵 PLAY_NEXT - Current: {guild_player['current_song'].title if guild_player['current_song'] else 'None'}")
#         print(f"🎵 PLAY_NEXT - Queue: {len(guild_player['queue'])} songs")
        
#         # Delay untuk menghindari race condition
#         await asyncio.sleep(0.5)
        
#         # Reset skip flag jika ada
#         if 'skip_requested' in guild_player:
#             guild_player['skip_requested'] = False
        
#         # LOGIC PEMUTARAN - SEDERHANA DAN PASTI BEKERJA
#         next_song = None
        
#         # 1. Cek jika loop aktif
#         if guild_player.get('loop', False) and guild_player['current_song']:
#             next_song = guild_player['current_song']
#             print(f"🎵 PLAY_NEXT - Looping: {next_song.title}")
        
#         # 2. Cek jika ada queue
#         elif guild_player['queue']:
#             next_song = guild_player['queue'].pop(0)
#             guild_player['current_song'] = next_song
#             print(f"🎵 PLAY_NEXT - Playing next from queue: {next_song.title}")
            
#             # Jika loop queue aktif, tambahkan kembali ke akhir
#             if guild_player.get('loop_queue', False):
#                 guild_player['queue'].append(next_song)
#                 print(f"🎵 PLAY_NEXT - Added to queue loop")
        
#         # 3. Tidak ada lagu berikutnya
#         else:
#             print(f"🎵 PLAY_NEXT - Queue empty")
#             guild_player['current_song'] = None
#             return
        
#         # Play lagu berikutnya
#         if next_song:
#             # Kirim pesan "Now Playing" jika ada context
#             if ctx and hasattr(ctx, 'channel'):
#                 try:
#                     embed = discord.Embed(
#                         description=f"🎶 Now playing: [{next_song.title}]({next_song.url})",
#                         color=0x00ff00
#                     )
#                     embed.set_footer(text=f"Requested by {next_song.requester.display_name}")
#                     if next_song.thumbnail:
#                         embed.set_thumbnail(url=next_song.thumbnail)
#                     await ctx.channel.send(embed=embed)
#                 except Exception as e:
#                     print(f"⚠️ Could not send now playing message: {e}")
            
#             # Play song dengan context
#             await play_song(voice_client, next_song, ctx)
            
#     except Exception as e:
#         print(f"❌ Error in play_next: {e}")
#         import traceback
#         traceback.print_exc()

async def play_next(ctx=None, guild_id=None):
    """Play next song in queue dengan berbagai cara pemanggilan"""
    try:
        # Dapatkan guild_id dari parameter atau ctx
        if guild_id is None:
            if ctx is None:
                print("❌ PLAY_NEXT - No context or guild_id provided")
                return
            
            guild_id = ctx.guild.id
            voice_client = ctx.voice_client
            guild_player = get_guild_player(ctx)
        else:
            # Cari voice client berdasarkan guild_id
            voice_client = None
            for vc in bot.voice_clients:
                if vc.guild.id == guild_id:
                    voice_client = vc
                    break
            
            if not voice_client:
                print(f"❌ PLAY_NEXT - No voice client for guild {guild_id}")
                return
            
            # Dapatkan guild_player dari storage
            guild_player = guild_players.get(guild_id)
            if not guild_player:
                print(f"❌ PLAY_NEXT - No guild player for guild {guild_id}")
                return
        
        # Cek jika voice_client masih valid
        if not voice_client or not voice_client.is_connected():
            print(f"❌ PLAY_NEXT - Voice client not connected for guild {guild_id}")
            return
        
        # Cek jika sedang dalam proses skip
        if guild_player.get('skip_requested', False):
            print(f"⚠️ PLAY_NEXT - Skip in progress for guild {guild_id}, aborting")
            guild_player['skip_requested'] = False
            return
        
        print(f"🎵 PLAY_NEXT - Guild: {guild_id}")
        print(f"🎵 PLAY_NEXT - Current: {guild_player['current_song'].title if guild_player['current_song'] else 'None'}")
        print(f"🎵 PLAY_NEXT - Queue: {len(guild_player['queue'])} songs")
        print(f"🎵 PLAY_NEXT - Loop: {guild_player['loop']}, Loop Queue: {guild_player['loop_queue']}")
        
        # Delay kecil untuk menghindari race condition
        await asyncio.sleep(0.5)
        
        # Cek jika masih playing (kadang callback dipanggil tapi masih playing)
        if voice_client.is_playing():
            print(f"⚠️ PLAY_NEXT - Still playing, waiting...")
            await asyncio.sleep(1)
            if voice_client.is_playing():
                print(f"❌ PLAY_NEXT - Still playing after wait, aborting")
                return
        
        # LOGIC PEMUTARAN
        if guild_player['loop'] and guild_player['current_song']:
            # Loop current song
            current_song = guild_player['current_song']
            print(f"🎵 PLAY_NEXT - Looping: {current_song.title}")
            await play_song(voice_client, current_song)
            
        elif guild_player['queue']:
            # Play next song in queue
            next_song = guild_player['queue'].pop(0)
            guild_player['current_song'] = next_song
            print(f"🎵 PLAY_NEXT - Playing next: {next_song.title}")
            
            # Update status di text channel yang benar
            try:
                # Get the text channel from guild_player (preferred) or from ctx
                text_channel = None
                
                # Option 1: Use stored text channel
                if guild_player.get('text_channel'):
                    text_channel = guild_player['text_channel']
                # Option 2: Use ctx if available
                elif ctx and hasattr(ctx, 'channel'):
                    text_channel = ctx.channel
                    # Also update the stored text channel
                    guild_player['text_channel'] = text_channel
                # Option 3: Try to find any text channel the bot can send to
                else:
                    # Get the first text channel the bot can send messages to
                    guild = voice_client.guild
                    for channel in guild.text_channels:
                        if channel.permissions_for(guild.me).send_messages:
                            text_channel = channel
                            break
                
                if text_channel:
                    embed = discord.Embed(
                        description=f"🎶 Now playing: [{next_song.title}]({next_song.url})",
                        color=0x00ff00
                    )
                    embed.set_footer(text=f"Requested by {next_song.requester.display_name if hasattr(next_song.requester, 'display_name') else 'Unknown'}")
                    if next_song.thumbnail:
                        embed.set_thumbnail(url=next_song.thumbnail)
                    await text_channel.send(embed=embed)
            except Exception as e:
                print(f"⚠️ Could not send now playing message: {e}")
            
            await play_song(voice_client, next_song)
            
            # Jika loop queue, tambahkan kembali ke akhir queue
            if guild_player['loop_queue']:
                guild_player['queue'].append(next_song)
                print(f"🎵 PLAY_NEXT - Queue loop: added {next_song.title} to end")
                
        else:
            # No more songs
            print(f"🎵 PLAY_NEXT - Queue empty for guild {guild_id}")
            guild_player['current_song'] = None
            
    except Exception as e:
        print(f"❌ Error in play_next: {e}")
        import traceback
        traceback.print_exc()

# ============================
# MEDIA DOWNLOAD FUNCTIONS
# ============================
UPLOAD_URL = "https://put.icu/upload/"

async def upload_to_puticu(path: str):
    """Upload file ke put.icu seperti script Python pertama (versi async)"""
    filename = os.path.basename(path)

    try:
        file_size = os.path.getsize(path)
    except:
        file_size = 0

    async with aiohttp.ClientSession() as session:
        with open(path, "rb") as f:
            async with session.put(
                UPLOAD_URL, 
                data=f,
                headers={"Accept": "application/json"},
                timeout=60
            ) as resp:
                if resp.status != 200:
                    return None
                
                try:
                    data = await resp.json()
                except:
                    return None
                
                return data.get("direct_url")  # URL akhir


async def download_media(ctx, url, mode):
    """Download media lalu upload otomatis ke put.icu"""
    if not YTDL_AVAILABLE:
        await ctx.send("❌ yt-dlp not available. Download features disabled.")
        return

    processing_msg = await ctx.send("⏳ Sedang memproses permintaanmu...")

    # Generate unique ID untuk file ini
    unique_id = str(uuid.uuid4())[:8]  # 8 karakter pertama dari UUID
    base_filename = f"temp_download_{unique_id}"
    
    # Template path dengan nama unik
    outtmpl_template = os.path.join(DOWNLOADS_PATH, f'{base_filename}.%(ext)s')
    
    ydl_opts = {
        'outtmpl': outtmpl_template,  # Gunakan template dengan ID unik
        'quiet': True,
        'noplaylist': True,
        'no_warnings': True,
    }

    if mode == 'yt':
        ydl_opts.update({
            'format': 'best[height<=1080]/best[ext=mp4]',
            'merge_output_format': 'mp4'
        })
    elif mode == 'ytmp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        })
    elif mode in ['fb', 'ig']:
        ydl_opts.update({
            'format': 'best[ext=mp4]/best',
            'merge_output_format': 'mp4'
        })
    else:
        await ctx.send("🚫 Mode tidak dikenal. Gunakan: `yt`, `ytmp3`, `fb`, atau `ig`.")
        return

    # Variabel untuk menyimpan info file
    downloaded_filename = None
    
    try:
        loop = asyncio.get_event_loop()
        ydl = youtube_dl.YoutubeDL(ydl_opts)
        
        # Ekstrak info tanpa download dulu untuk mendapatkan ekstensi
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
        is_music = 'music.youtube.com' in url.lower() or info.get('extractor') == 'youtube:tab'
        
        # Dapatkan ekstensi file dari info
        original_ext = info.get('ext', 'mp4')  # Default ke mp4 jika tidak ada
        
        # Tentukan ekstensi berdasarkan mode
        if mode == 'ytmp3':
            expected_ext = 'mp3'
            downloaded_filename = os.path.join(DOWNLOADS_PATH, f'{base_filename}.mp3')
        else:
            expected_ext = original_ext if original_ext in ['mp4', 'webm', 'mkv'] else 'mp4'
            downloaded_filename = os.path.join(DOWNLOADS_PATH, f'{base_filename}.{expected_ext}')
        
        # Download file
        await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
        
        # Coba cari file dengan berbagai kemungkinan ekstensi
        if not os.path.exists(downloaded_filename):
            # Cari file dengan pattern yang cocok
            possible_files = []
            for file in os.listdir(DOWNLOADS_PATH):
                if file.startswith(base_filename):
                    possible_files.append(file)
            
            if possible_files:
                # Ambil file pertama yang ditemukan
                downloaded_filename = os.path.join(DOWNLOADS_PATH, possible_files[0])
            else:
                # Fallback: cari file dengan ekstensi umum
                for ext in ['mp4', 'mp3', 'webm', 'mkv', 'm4a']:
                    fallback_path = os.path.join(DOWNLOADS_PATH, f'{base_filename}.{ext}')
                    if os.path.exists(fallback_path):
                        downloaded_filename = fallback_path
                        break
        
        if not os.path.exists(downloaded_filename):
            await ctx.send("❌ File hasil unduhan tidak ditemukan.")
            return

        title = info.get('title', 'Unknown Title')
        webpage_url = info.get('webpage_url', url)
        thumbnail = info.get('thumbnail')

        # ============================
        # 📤 UPLOAD KE PUT.ICU
        # ============================
        # await processing_msg.edit(content="📤 Mengupload ke server...")

        uploaded_url = await upload_to_puticu(downloaded_filename)

        if not uploaded_url:
            await processing_msg.edit(content="❌ Upload gagal.")
            return

        # ============================
        # 📦 KIRIM EMBED + LINK
        # ============================
        await processing_msg.edit(content=uploaded_url)

    except Exception as e:
        await ctx.send(f"❌ Terjadi error: `{e}`")
        import traceback
        print(f"Error details: {traceback.format_exc()}")

    finally:
        # Bersihkan file yang diunduh
        try:
            if downloaded_filename and os.path.exists(downloaded_filename):
                os.remove(downloaded_filename)
                print(f"Cleaned up: {downloaded_filename}")
            
            # Juga bersihkan file lain dengan base_filename yang sama
            if 'base_filename' in locals():
                pattern = os.path.join(DOWNLOADS_PATH, f"{base_filename}.*")
                import glob
                for leftover_file in glob.glob(pattern):
                    try:
                        os.remove(leftover_file)
                        print(f"Cleaned up leftover: {leftover_file}")
                    except:
                        pass
        except Exception as cleanup_error:
            print(f"Cleanup error: {cleanup_error}")
        
# ============================
# GIF CONVERSION FUNCTIONS
# ============================

async def convert_image_to_gif_fixed(input_path, output_path):
    """Convert image to GIF dengan teknik darken white"""
    if not PIL_AVAILABLE:
        raise Exception("PIL/Pillow not available")
    
    with Image.open(input_path) as img:
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        img_data = img.getdata()
        new_data = []
        
        for pixel in img_data:
            r, g, b = pixel
            if r > 240 and g > 240 and b > 240:
                r = max(200, r - 5)
                g = max(200, g - 5)
                b = max(200, b - 5)
            new_data.append((r, g, b))
        
        img.putdata(new_data)
        img_rgba = img.convert('RGBA')
        
        width, height = img_rgba.size
        if width > 0 and height > 0:
            img_rgba.putpixel((0, 0), (0, 0, 0, 0))
        
        img_rgba.save(output_path, format='GIF', transparency=0, optimize=True)

async def convert_video_to_gif_fixed(input_path, output_path):
    """Convert video to GIF"""
    if not MOVIEPY_AVAILABLE:
        await convert_video_simple_ffmpeg(input_path, output_path)
        return
    
    try:
        clip = mp.VideoFileClip(input_path)
        max_duration = 5
        
        if clip.duration > max_duration:
            clip = clip.subclip(0, max_duration)
        
        target_width = 320
        if clip.w > target_width:
            clip = clip.resize(width=target_width)
        
        fps = min(8, clip.fps)
        
        clip.write_gif(
            output_path,
            fps=fps,
            program='ffmpeg',
            tempfiles=True,
            logger=None
        )
        
        clip.close()
        
    except Exception as e:
        await convert_video_simple_ffmpeg(input_path, output_path)

async def convert_video_simple_ffmpeg(input_path, output_path):
    """Simple video to GIF menggunakan ffmpeg"""
    import subprocess
    
    try:
        cmd_convert = [
            'ffmpeg', '-i', input_path,
            '-t', '5',
            '-vf', 'fps=8,scale=320:-1',
            '-y', output_path
        ]
        
        result = subprocess.run(cmd_convert, capture_output=True, timeout=30)
        if result.returncode != 0:
            raise Exception(f"FFmpeg failed with return code {result.returncode}")
        
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg timeout")
    except Exception as e:
        raise Exception(f"Video conversion error: {str(e)}")

# ============================
# WAIFU SYSTEM FUNCTIONS
# ============================

async def handle_waifu_claim(ctx, force_character=None):
    """Handle daily waifu claiming dengan opsi force character untuk admin"""
    waifu_folder = "./images/waifu"
    claim_file = "claimed_waifus.json"
    
    # Daftar admin IDs (ganti dengan ID Discord kamu)
    ADMIN_IDS = [869897744972668948]  # Ganti dengan ID Discord kamu
    
    # Jika ada force_character dan user adalah admin
    if force_character and ctx.author.id in ADMIN_IDS:
        user_id = str(ctx.author.id)
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Load data existing
        if not os.path.exists(claim_file):
            with open(claim_file, "w") as f:
                json.dump({}, f)
        
        try:
            with open(claim_file, "r") as f:
                content = f.read().strip()
                data = json.loads(content) if content else {}
        except json.JSONDecodeError:
            data = {}
        
        # Cari file yang sesuai dengan karakter yang diminta
        waifus = [f for f in os.listdir(waifu_folder) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        
        # Normalisasi nama karakter yang diminta
        force_character_lower = force_character.lower()
        matching_files = []
        
        for waifu_file in waifus:
            waifu_name = os.path.splitext(waifu_file)[0].lower()
            # Cek apakah nama file mengandung karakter yang diminta
            if force_character_lower in waifu_name:
                matching_files.append(waifu_file)
        
        if not matching_files:
            await ctx.send(f"❌ Tidak ditemukan karakter dengan nama **{force_character}** di folder waifu.")
            return
        
        # Pilih file pertama yang cocok, atau random jika ada banyak
        chosen = matching_files[0] if len(matching_files) == 1 else random.choice(matching_files)
        waifu_name = os.path.splitext(chosen)[0].replace("_", " ").title()
        
        # Update data
        old_data = data.get(user_id, {})
        old_count = old_data.get("count", 0)
        
        data[user_id] = {
            "date": today,
            "waifu": waifu_name,
            "count": old_count + 1,
            "forced": True  # Flag untuk menandai ini force claim
        }
        
        with open(claim_file, "w") as f:
            json.dump(data, f, indent=4)
        
        await ctx.send(f"💘 Hari ini bebebmu adalah **{waifu_name}**! 💞")
        
        try:
            await ctx.send(file=File(os.path.join(waifu_folder, chosen)))
        except discord.HTTPException:
            await ctx.send(f"⚠️ Gambar **{waifu_name}** terlalu besar untuk dikirim.")
        
        return
    
    # NORMAL CLAIM (kode asli)
    if not os.path.exists(waifu_folder):
        await ctx.send("📁 Folder waifu tidak ditemukan!")
        return

    if not os.path.exists(claim_file):
        with open(claim_file, "w") as f:
            json.dump({}, f)

    try:
        with open(claim_file, "r") as f:
            content = f.read().strip()
            data = json.loads(content) if content else {}
    except json.JSONDecodeError:
        data = {}

    user_id = str(ctx.author.id)
    today = datetime.now().strftime("%Y-%m-%d")

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

    old_data = data.get(user_id, {})
    old_count = old_data.get("count", 0)

    data[user_id] = {
        "date": today,
        "waifu": waifu_name,
        "count": old_count + 1,
        "forced": False
    }

    with open(claim_file, "w") as f:
        json.dump(data, f, indent=4)

    await ctx.send(f"💘 Hari ini bebebmu adalah **{waifu_name}**! 💞")

    try:
        await ctx.send(file=File(os.path.join(waifu_folder, chosen)))
    except discord.HTTPException:
        await ctx.send(f"⚠️ Gambar **{waifu_name}** terlalu besar untuk dikirim.")

async def get_top_karbit(ctx):
    """Show waifu claim leaderboard"""
    claim_file = "claimed_waifus.json"

    if not os.path.exists(claim_file):
        await ctx.send("📂 Belum ada data claim.")
        return

    try:
        with open(claim_file, "r") as f:
            content = f.read().strip()
            data = json.loads(content) if content else {}
    except json.JSONDecodeError:
        data = {}

    if not data:
        await ctx.send("📭 Belum ada yang claim waifu.")
        return

    leaderboard = []
    for user_id, info in data.items():
        count = info.get("count", 0)
        leaderboard.append((user_id, count))

    leaderboard.sort(key=lambda x: x[1], reverse=True)

    desc = ""
    for i, (user_id, count) in enumerate(leaderboard[:10], start=1):
        try:
            user = await ctx.bot.fetch_user(int(user_id))
            desc += f"**{i}.** {user.name} — ❤️ {count}x claim\n"
        except:
            desc += f"**{i}.** Unknown User — ❤️ {count}x claim\n"

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
    """Bot startup handler"""
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"{PREFIX}help"))
    
    # Load MALCommands cog
    try:
        await bot.add_cog(MALCommands(bot))
        print("✅ MALCommands cog loaded successfully!")
    except Exception as e:
        print(f"❌ Failed to load MALCommands cog: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    """Auto-disconnect jika sendirian di voice channel"""
    try:
        # Case 1: Bot sendiri yang disconnect
        if member == bot.user and not after.channel:
            if before.channel:
                guild_id = before.channel.guild.id
                player.clear_guild(guild_id)
    
        # Case 2: Member lain keluar dari channel
        if member != bot.user and before.channel and not after.channel:
            voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)
            if voice_client and voice_client.channel == before.channel:
                if len(voice_client.channel.members) == 1:
                    guild_id = member.guild.id
                    player.clear_guild(guild_id)
                    
                    if voice_client.is_playing() or voice_client.is_paused():
                        voice_client.stop()
                    
                    await asyncio.sleep(1)
                    await voice_client.disconnect()
                    
    except Exception as e:
        print(f"Voice state update error: {e}")

@bot.event
async def on_message(message):
    """Handle auto-replies and command processing"""
    if message.author.bot:
        return

    # Process bans first
    cleanup_expired_timeouts()
    data = load_bans()
    if str(message.author.id) in data:
        await bot.process_commands(message)
        return

    content = message.content.lower()
    
    replies = {
        "jawa": "jawa lagi, jawa lagi",
        "nkj karbit": "maaf, nkj tidak karbit",
        "my kisah": "karbitnyooo",
        "bukankah ini": "bukan",
        "samsul": "habis bensin",
        "nkj anjeng": "you're done lil bro\n\nIP. 92.28.211.23\nN: 43.7462\nW: 12.4893 SS Number: 6979191519182043\nIPv6: fe80:5dcd.:ef69:fb22::d9 \nUPP: Enabled DMZ: 10.112.42\nMAC: 5A:78:3:7E:00\nDNS: 8.8.8.8\nALT DNS: 1.1.1.8.1\nDNS SUFFIX: Dink WAN: 100.236\nGATEWAY: 192.168\nUDP OPEN PORT: 8080.80",
        "dika": "dika anjeng",
        "osu": "yah ada osu, bete gw njing",
        "help me reika": "In case of an investigation by any federal entity or similar, I do not have any involvement with this group or with the people in it, I do not know how I am here, probably added by a third party, I do not support any actions by members of this group.",
        "lala": "Bete njing ada lala",
        "bedwar": "bising bodo aku nak tido",
        "my bebeb": "karbit bgt njeng",
        "reika": "ap sh manggil manggil, nanti bebeb nkj marah lho",
        "saran lagu": "https://youtu.be/UqFkguq89YU?si=CF6t_IEAlTnR3p9z",
        "kimi thread": "‼Kimi Thread ‼\nThis is going to be a thread on Kimi (also known as SakudaPikora, MrMolvanstress) and his inappropriate behavior with minors. As well as allowing minors into his discord server that is based off of his YouTube channel (which is very sexual in nature). I'm censoring the name of all minors to avoid exposing them to undesirables"    
    }
    
    for k, v in replies.items():
        if k in content:
            await message.channel.send(v)
            break

    await bot.process_commands(message)

# ============================
# MUSIC COMMANDS
# ============================

@bot.command(aliases=['p'])
async def play(ctx, *, query):
    """Play a song or add to queue - UPDATED"""
    if not YTDL_AVAILABLE:
        await ctx.send("❌ Music features are currently unavailable.")
        return

    if not ctx.author.voice:
        await ctx.send("🚫 You need to be in a voice channel!")
        return

    clean_query = query.strip()
    if not clean_query:
        await ctx.send("🚫 Please provide a song name or URL")
        return

    voice_client = ctx.voice_client
    if not voice_client:
        try:
            await ctx.author.voice.channel.connect()
        except Exception as e:
            await ctx.send(f"❌ Failed to connect to voice channel: {e}")
            return

    status_msg = await ctx.send("🎧 Searching for the song, please wait...")
    guild_player = get_guild_player(ctx)

    async with ctx.typing():
        try:
            if 'list=' in clean_query.lower() and ('youtube.com' in clean_query.lower() or 'youtu.be' in clean_query.lower()):
                # Playlist handling - SUPPORT 1000+ SONGS
                try:
                    ytdl_playlist = youtube_dl.YoutubeDL({
                        **ytdl_format_options,
                        'extract_flat': True,
                        'noplaylist': False,
                        'quiet': True,
                        'no_warnings': True,
                    })

                    playlist_data = await bot.loop.run_in_executor(
                        None,
                        lambda: ytdl_playlist.extract_info(clean_query, download=False)
                    )

                    if not playlist_data or 'entries' not in playlist_data:
                        await status_msg.edit(content="❌ Couldn't process that playlist or playlist is empty")
                        return

                    # Process semua songs tanpa batasan 100
                    songs = []
                    total_entries = len(playlist_data['entries'])
                    
                    # Kirim status processing
                    await status_msg.edit(content=f"🔄 Processing playlist... (0/{total_entries} songs)")
                    
                    for i, entry in enumerate(playlist_data['entries']):
                        if entry:
                            try:
                                song = Song(entry, ctx.author)
                                songs.append(song)
                                
                                # Update status setiap 50 songs agar tidak spam
                                if i % 50 == 0:
                                    await status_msg.edit(content=f"🔄 Processing playlist... ({i}/{total_entries} songs)")
                                
                                # Optional: Batas maksimal 1000 songs untuk prevent abuse
                                if len(songs) >= 1000:
                                    await ctx.send(f"⚠️ Playlist terlalu besar! Hanya mengambil 1000 lagu pertama.")
                                    break
                                    
                            except Exception as e:
                                print(f"Error processing playlist entry {i}: {e}")
                                continue

                    if not songs:
                        await status_msg.edit(content="❌ No valid songs found in playlist")
                        return

                    # Add songs to queue
                    guild_player['playlist_mode'] = True
                    for song in songs:
                        guild_player['queue'].append(song)
                    guild_player['playlist_mode'] = False

                    # Mainkan jika belum main
                    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                        await play_next(ctx)

                    await status_msg.edit(content=f"🎵 Added {len(songs)} songs from playlist: **{playlist_data['title']}**")

                except Exception as e:
                    await status_msg.edit(content=f"❌ Playlist error: {str(e)}")

            else:
                # Single song handling - PASTIKAN CONTEXT DITERUSKAN
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

                        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                            guild_player['queue'].append(song)
                            embed = discord.Embed(
                                description=f"🎵 Added to queue: [{song.title}]({song.url})",
                                color=0x00ff00
                            )
                            embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                            await status_msg.edit(content=None, embed=embed)
                        else:
                            guild_player['current_song'] = song
                            # ⭐⭐ PASTIKAN CONTEXT DITERUSKAN KE play_song ⭐⭐
                            await play_song(ctx.voice_client, song, ctx)
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
    """Show current queue with navigation buttons"""
    guild_player = get_guild_player(ctx)
    
    if not guild_player['queue'] and not guild_player['current_song']:
        await ctx.send("ℹ️ The queue is empty!")
        return

    # Tentukan jumlah item per halaman
    items_per_page = 8  # Reduced from 10 to be safer
    total_songs = len(guild_player['queue'])
    total_pages = max(1, (total_songs + items_per_page - 1) // items_per_page)
    
    # Validasi nomor halaman
    page = max(1, min(page, total_pages))
    
    # Fungsi untuk membuat embed berdasarkan halaman
    def create_embed(page_num):
        embed = discord.Embed(
            title="🎧 Music Queue",
            color=0x00ff00,
            timestamp=ctx.message.created_at
        )
        
        # Tambahkan current song
        if guild_player['current_song']:
            current = guild_player['current_song']
            embed.add_field(
                name="🎶 Now Playing",
                value=(
                    f"[{current.title}]({current.url})\n"
                    f"⏳ {current.format_duration()} | "
                    f"Requested by {current.requester.mention}"
                ),
                inline=False
            )
        
        # Tambahkan separator jika ada current song
        if guild_player['current_song'] and guild_player['queue']:
            embed.add_field(name="\u200b", value="──────────────", inline=False)
        
        # Tambahkan queue songs untuk halaman ini
        if guild_player['queue']:
            start = (page_num - 1) * items_per_page
            end = min(start + items_per_page, total_songs)
            
            queue_text = ""
            for i in range(start, end):
                song = guild_player['queue'][i]
                position = i + 1
                
                # Format song entry dengan truncation yang aman
                song_title = song.title
                # Truncate title if too long
                if len(song_title) > 60:
                    song_title = f"{song_title[:57]}..."
                
                # Build the entry
                entry = f"`{position}.` [{song_title}]({song.url})\n"
                entry += f"    ⏳ {song.format_duration()} | 👤 {song.requester.display_name}\n"
                
                # Check if adding this entry would exceed the limit
                if len(queue_text) + len(entry) > 1000:  # Leave some buffer
                    queue_text += f"\n*... and {end - i} more songs*"
                    break
                
                queue_text += entry
            
            # Jika ada text, tambahkan ke embed
            if queue_text:
                embed.add_field(
                    name=f"📜 Up Next (Songs {start+1}-{end} of {total_songs})",
                    value=queue_text,
                    inline=False
                )
        
        # Tambahkan footer dengan status dan halaman
        footer_text = f"Page {page_num}/{total_pages}"
        
        # Tambahkan status loop
        status_parts = []
        if guild_player['loop']:
            status_parts.append("🔂 Loop")
        if guild_player['loop_queue']:
            status_parts.append("🔁 Queue Loop")
        
        if status_parts:
            footer_text += f" • {' | '.join(status_parts)}"
        
        embed.set_footer(text=footer_text)
        return embed
    
    # Buat dan kirim embed awal
    embed = create_embed(page)
    message = await ctx.send(embed=embed)
    
    # Jika ada lebih dari 1 halaman, tambahkan buttons
    if total_pages > 1:
        # Tambahkan reactions/buttons
        if page > 1:
            await message.add_reaction("◀️")  # Previous
        if page < total_pages:
            await message.add_reaction("▶️")  # Next
        await message.add_reaction("❌")  # Close/Stop
        
        # Fungsi untuk mengecek reaction
        def check(reaction, user):
            return (
                user == ctx.author and
                reaction.message.id == message.id and
                str(reaction.emoji) in ["◀️", "▶️", "❌"]
            )
        
        # Reaction handler
        try:
            while True:
                reaction, user = await bot.wait_for('reaction_add', timeout=60.0, check=check)
                
                # Hapus reaction user
                try:
                    await message.remove_reaction(reaction.emoji, user)
                except:
                    pass
                
                # Handle reaction
                if str(reaction.emoji) == "◀️" and page > 1:
                    page -= 1
                elif str(reaction.emoji) == "▶️" and page < total_pages:
                    page += 1
                elif str(reaction.emoji) == "❌":
                    try:
                        await message.clear_reactions()
                    except:
                        pass
                    break
                
                # Update embed
                new_embed = create_embed(page)
                await message.edit(embed=new_embed)
                
                # Update reactions jika perlu
                try:
                    await message.clear_reactions()
                    if page > 1:
                        await message.add_reaction("◀️")
                    if page < total_pages:
                        await message.add_reaction("▶️")
                    await message.add_reaction("❌")
                except:
                    pass
        
        except asyncio.TimeoutError:
            # Hapus reactions setelah timeout
            try:
                await message.clear_reactions()
            except:
                pass

@bot.command(aliases=['s'])
async def skip(ctx):
    """Skip current song - FIXED VERSION"""
    voice_client = ctx.voice_client
    if not voice_client or not voice_client.is_playing():
        await ctx.send("ℹ️ Nothing is currently playing!")
        return
    
    guild_player = get_guild_player(ctx)
    guild_id = ctx.guild.id
    
    print(f"⏭️ SKIP - Current: {guild_player['current_song'].title if guild_player['current_song'] else 'None'}")
    
    # Set skip flag
    guild_player['skip_requested'] = True
    
    # Stop current playback
    voice_client.stop()
    
    await asyncio.sleep(0.5)
    
    # Langsung panggil play_next
    await play_next(ctx)
    
    await ctx.message.add_reaction("⏭️")

@bot.command()
async def loop(ctx):
    """Toggle loop for current song"""
    guild_player = get_guild_player(ctx)
    guild_player['loop'] = not guild_player['loop']
    guild_player['loop_queue'] = False if guild_player['loop'] else guild_player['loop_queue']
    await ctx.message.add_reaction("🔂" if guild_player['loop'] else "➡️")

@bot.command()
async def loopqueue(ctx):
    """Toggle queue looping"""
    guild_player = get_guild_player(ctx)
    guild_player['loop_queue'] = not guild_player['loop_queue']
    guild_player['loop'] = False if guild_player['loop_queue'] else guild_player['loop']
    await ctx.message.add_reaction("🔁" if guild_player['loop_queue'] else "➡️")

@bot.command(aliases=['rm'])
async def remove(ctx, index: int):
    """Remove a song from queue"""
    guild_player = get_guild_player(ctx)
    if not guild_player['queue']:
        await ctx.send("ℹ️ The queue is empty!")
        return
    
    if index < 1 or index > len(guild_player['queue']):
        await ctx.send(f"🚫 Please provide a valid position (1-{len(guild_player['queue'])})")
        return
    
    removed = guild_player['queue'].pop(index - 1)
    embed = discord.Embed(
        description=f"🗑️ Removed: [{removed.title}]({removed.url})",
        color=0x00ff00
    )
    embed.set_footer(text=f"Was position {index} | Requested by {removed.requester.display_name}")
    await ctx.send(embed=embed)

@bot.command(aliases=['c'])
async def clear(ctx):
    """Clear the queue"""
    guild_player = get_guild_player(ctx)
    if not guild_player['queue']:
        await ctx.send("ℹ️ The queue is already empty!")
        return
    
    guild_player['queue'].clear()
    await ctx.message.add_reaction("🧹")

@bot.command(aliases=['vol'])
async def volume(ctx, volume: int = None):
    """Set volume (0-100)"""
    guild_player = get_guild_player(ctx)
    if volume is None:
        await ctx.send(f"🔊 Current volume: {int(guild_player['volume'] * 100)}%")
        return
    
    if volume < 0 or volume > 100:
        await ctx.send("🚫 Volume must be between 0 and 100")
        return
    
    guild_player['volume'] = volume / 100
    if ctx.voice_client and ctx.voice_client.source:
        ctx.voice_client.source.volume = guild_player['volume']
    
    await ctx.message.add_reaction("🔊")

@bot.command()
async def shuffle(ctx):
    """Shuffle the queue"""
    guild_player = get_guild_player(ctx)
    if len(guild_player['queue']) < 2:
        await ctx.send("ℹ️ Need at least 2 songs in queue to shuffle!")
        return
    
    import random
    random.shuffle(guild_player['queue'])
    await ctx.message.add_reaction("🔀")

@bot.command(aliases=['mv'])
async def move(ctx, from_pos: int, to_pos: int):
    """Move song in queue"""
    guild_player = get_guild_player(ctx)
    if len(guild_player['queue']) < 2:
        await ctx.send("ℹ️ Need at least 2 songs in queue to move!")
        return
    
    if from_pos < 1 or from_pos > len(guild_player['queue']) or to_pos < 1 or to_pos > len(guild_player['queue']):
        await ctx.send(f"🚫 Invalid positions (1-{len(guild_player['queue'])})")
        return
    
    from_idx = from_pos - 1
    to_idx = to_pos - 1
    
    if from_idx == to_idx:
        await ctx.send("🚫 Positions are the same!")
        return
    
    moved_song = guild_player['queue'].pop(from_idx)
    guild_player['queue'].insert(to_idx, moved_song)
    
    embed = discord.Embed(
        description=f"↕️ Moved [{moved_song.title}]({moved_song.url}) from position {from_pos} to {to_pos}",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@bot.command(aliases=['l', 'lyric'])
async def lyrics(ctx, *, song_name: str = None):
    """Get lyrics for current song or specified song"""
    if not genius:
        await ctx.send("❌ Lyrics feature is not configured. Please set up Genius API key.")
        return
    
    if song_name is None:
        guild_player = get_guild_player(ctx)
        if not guild_player['current_song']:
            await ctx.send("❌ No song is currently playing! Please specify a song name.")
            return
        
        song_name = guild_player['current_song'].title
        clean_name = song_name.split(' (Official')[0].split(' | ')[0].split(' [Audio]')[0]
        search_query = clean_name
    else:
        search_query = song_name
    
    status_msg = await ctx.send(f"🔍 Searching lyrics for **{search_query}**...")
    
    try:
        song = await bot.loop.run_in_executor(
            None,
            lambda: genius.search_song(search_query)
        )
        
        if not song:
            await status_msg.edit(content=f"❌ No lyrics found for **{search_query}**")
            return
        
        lyrics_text = song.lyrics
        
        if len(lyrics_text) > 2000:
            filename = f"lyrics_{song.title.replace(' ', '_')}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"Lyrics for: {song.title}\n")
                f.write(f"Artist: {song.artist}\n")
                f.write("="*50 + "\n\n")
                f.write(lyrics_text)
            
            await status_msg.delete()
            await ctx.send(
                f"📝 **{song.title}** by **{song.artist}**",
                file=discord.File(filename)
            )
            
            os.remove(filename)
        else:
            embed = discord.Embed(
                title=f"🎵 {song.title}",
                description=f"by **{song.artist}**",
                color=0x00ff00
            )
            embed.add_field(
                name="Lyrics",
                value=lyrics_text[:1020] + "..." if len(lyrics_text) > 1020 else lyrics_text,
                inline=False
            )
            
            if hasattr(song, 'album_art') and song.album_art:
                embed.set_thumbnail(url=song.album_art)
            
            await status_msg.delete()
            await ctx.send(embed=embed)
            
    except Exception as e:
        await status_msg.edit(content=f"❌ Error fetching lyrics: {str(e)}")

@bot.command(aliases=['leave', 'disconnect', 'dc'])
async def stop(ctx):
    """Stop playback and disconnect"""
    if not ctx.voice_client:
        await ctx.send("ℹ️ I'm not in a voice channel!")
        return
    
    guild_player = get_guild_player(ctx)
    guild_id = ctx.guild.id
    
    guild_player['queue'].clear()
    guild_player['current_song'] = None
    guild_player['loop'] = False
    guild_player['loop_queue'] = False
    
    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        ctx.voice_client.stop()
    
    await ctx.voice_client.disconnect()
    player.clear_guild(guild_id)
    await ctx.message.add_reaction("🛑")

# ============================
# MEDIA DOWNLOAD COMMANDS
# ============================

@bot.command()
async def yt(ctx, url: str):
    asyncio.create_task(download_media(ctx, url, 'yt'))

@bot.command()
async def ytmp3(ctx, url: str):
    asyncio.create_task(download_media(ctx, url, 'ytmp3'))

@bot.command()
async def fb(ctx, url: str):
    asyncio.create_task(download_media(ctx, url, 'fb'))


@bot.command()
async def ig(ctx, url: str):
    asyncio.create_task(download_media(ctx, url, 'ig'))

@bot.command(name="ytthumbnail")
async def ytthumbnail(ctx, url: str = None):
    """Get YouTube video thumbnail"""
    if url is None:
        await ctx.send("📺 Gunakan command seperti ini:\n`n.ytthumbnail <link_youtube>`")
        return

    pattern = r"(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]{11})"
    match = re.search(pattern, url)

    if not match:
        await ctx.send("⚠️ Tidak bisa menemukan ID video YouTube dari link itu.")
        return

    video_id = match.group(1)
    resolutions = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
    ]

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
    """Download video dari Twitter (X)"""
    await ctx.send("🐦 Sedang memproses video Twitter...")

    temp_filename = os.path.join(DOWNLOADS_PATH, "twitter_video.mp4")

    if os.path.exists(temp_filename):
        os.remove(temp_filename)

    ydl_opts = {
        "outtmpl": temp_filename,
        "format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }

    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        file_size = os.path.getsize(temp_filename)
        limit_bytes = 25 * 1024 * 1024

        if file_size <= limit_bytes:
            await ctx.send("✅ Video berhasil diunduh!", file=File(temp_filename))
        else:
            await ctx.send("⚠️ File terlalu besar, sedang diupload ke GoFile.io...")

            server_info = requests.get("https://api.gofile.io/getServer").json()
            if server_info["status"] != "ok":
                await ctx.send("❌ Gagal ambil server GoFile.io.")
                return

            server = server_info["data"]["server"]

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
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

@bot.command()
async def togif(ctx):
    """Convert image/video menjadi GIF (versi improved)"""
    if not PIL_AVAILABLE:
        await ctx.send("❌ GIF conversion is not available. PIL/Pillow not installed.")
        return

    await ctx.send("🎞️ Sedang mengubah ke GIF...")

    DOWNLOAD_LIMIT_BYTES = 25 * 1024 * 1024  # Tingkatkan limit ke 25MB

    attachment = None
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
    elif ctx.message.reference:
        ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        if ref.attachments:
            attachment = ref.attachments[0]

    if not attachment:
        await ctx.send("⚠️ Tidak ada file yang ditemukan. Kirim atau reply file gambar/video!")
        return

    if attachment.size > DOWNLOAD_LIMIT_BYTES:
        await ctx.send("⚠️ File terlalu besar (>25MB). Gunakan file yang lebih kecil!")
        return

    filename = os.path.join(DOWNLOADS_PATH, attachment.filename)
    await attachment.save(filename)

    output_path = os.path.splitext(filename)[0] + ".gif"

    try:
        if attachment.content_type.startswith("image/"):
            await convert_image_to_gif_improved(filename, output_path)
        elif attachment.content_type.startswith("video/"):
            await convert_video_to_gif_optimized(filename, output_path)
        else:
            await ctx.send("❌ Format file tidak didukung. Hanya gambar (jpg, png, jpeg) atau video (mp4, mov, avi)!")
            return

        if not os.path.exists(output_path):
            await ctx.send("❌ Gagal membuat GIF!")
            return

        file_size = os.path.getsize(output_path)
        if file_size > DOWNLOAD_LIMIT_BYTES:
            await ctx.send(f"⚠️ GIF hasilnya terlalu besar ({file_size/1024/1024:.1f}MB > 25MB). Coba file yang lebih pendek atau resolusi lebih kecil!")
            return

        file = discord.File(output_path, filename=os.path.basename(output_path))
        await ctx.send("✅ Berhasil dikonversi ke GIF!", file=file)

    except Exception as e:
        await ctx.send(f"❌ Terjadi error saat konversi: `{str(e)}`")
        print(f"Error in togif: {e}")

    finally:
        try:
            for f in [filename, output_path]:
                if os.path.exists(f):
                    os.remove(f)
        except Exception as e:
            print(f"Error cleaning up files: {e}")

# ============================
# IMPROVED GIF CONVERSION FUNCTIONS
# ============================

async def convert_image_to_gif_improved(input_path, output_path):
    """Convert image to GIF dengan mempertahankan warna putih"""
    if not PIL_AVAILABLE:
        raise Exception("PIL/Pillow not available")
    
    try:
        with Image.open(input_path) as img:
            # Convert ke RGB jika mode lain
            if img.mode not in ['RGB', 'RGBA']:
                img = img.convert('RGB')
            
            # Jika RGBA dengan transparansi, convert ke RGB tanpa alpha
            if img.mode == 'RGBA':
                # Buat background putih untuk area transparan
                white_bg = Image.new('RGB', img.size, (255, 255, 255))
                white_bg.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else None)
                img = white_bg
            
            # Optimasi untuk JPEG: pertahankan warna asli
            elif img.mode == 'RGB':
                # Untuk JPEG, kita hanya perlu mengonversi ke GIF tanpa modifikasi warna
                pass
            
            # Resize jika terlalu besar (max 800px width)
            max_width = 800
            if img.width > max_width:
                ratio = max_width / img.width
                new_height = int(img.height * ratio)
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            
            # Convert ke GIF dengan palette optimal
            img = img.convert('P', palette=Image.Palette.ADAPTIVE, colors=256)
            
            # Simpan dengan optimasi
            img.save(
                output_path, 
                format='GIF',
                save_all=True,
                optimize=True,
                loop=0,  # Loop forever
                duration=100  # 100ms per frame (untuk gambar tetap)
            )
            
    except Exception as e:
        raise Exception(f"Image conversion error: {str(e)}")

async def convert_video_to_gif_optimized(input_path, output_path):
    """Convert video to GIF dengan optimasi"""
    # Coba moviepy dulu, fallback ke ffmpeg langsung
    if MOVIEPY_AVAILABLE:
        await convert_video_with_moviepy(input_path, output_path)
    else:
        await convert_video_with_ffmpeg(input_path, output_path)

async def convert_video_with_moviepy(input_path, output_path):
    """Convert video menggunakan moviepy dengan pengaturan optimal"""
    try:
        clip = mp.VideoFileClip(input_path)
        
        # Batasi durasi (max 15 detik untuk GIF)
        max_duration = 15
        if clip.duration > max_duration:
            clip = clip.subclip(0, max_duration)
        
        # Resize untuk mengurangi ukuran
        target_width = 480  # Lebih kecil dari sebelumnya
        if clip.w > target_width:
            clip = clip.resize(width=target_width)
        
        # Optimasi FPS
        fps = min(10, clip.fps)  # Maksimal 10 fps untuk GIF
        
        # Buat palette terlebih dahulu untuk kualitas lebih baik
        temp_palette = output_path + "_palette.png"
        
        # Generate palette dulu
        palette_cmd = [
            'ffmpeg', '-i', input_path,
            '-vf', f'fps={fps},scale={target_width}:-1:flags=lanczos,palettegen',
            '-y', temp_palette
        ]
        
        import subprocess
        result = subprocess.run(palette_cmd, capture_output=True, timeout=30)
        if result.returncode != 0 and os.path.exists(temp_palette):
            os.remove(temp_palette)
        
        # Gunakan moviepy untuk konversi
        clip.write_gif(
            output_path,
            fps=fps,
            program='ffmpeg',
            opt='optimizeplus',
            fuzz=2,  # Allow sedikit perbedaan warna untuk kompresi lebih baik
            tempfiles=True
        )
        
        clip.close()
        
        # Clean up
        if os.path.exists(temp_palette):
            os.remove(temp_palette)
            
    except Exception as e:
        print(f"Moviepy conversion failed, falling back to ffmpeg: {e}")
        await convert_video_with_ffmpeg(input_path, output_path)

async def convert_video_with_ffmpeg(input_path, output_path):
    """Convert video ke GIF dengan 12 fps (12 frame per detik)"""
    import subprocess
    
    try:
        # Dapatkan info video dengan cara yang lebih reliable
        print("Getting video info...")
        
        # Coba dua cara untuk mendapatkan FPS
        original_fps = 30  # Default
        
        # Cara 1: Dapatkan FPS dari metadata
        try:
            fps_cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=avg_frame_rate',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                input_path
            ]
            
            fps_result = subprocess.run(fps_cmd, capture_output=True, text=True, timeout=10)
            if fps_result.returncode == 0 and fps_result.stdout.strip():
                fps_str = fps_result.stdout.strip()
                if '/' in fps_str:
                    num, den = fps_str.split('/')
                    if num and den:
                        original_fps = float(num) / float(den)
                        print(f"Got FPS from metadata: {original_fps:.2f}")
        except:
            pass
        
        # Cara 2: Jika FPS aneh (< 10), coba dengan cara lain
        if original_fps < 10:
            print(f"Suspicious FPS ({original_fps:.2f}), trying alternative method...")
            try:
                # Cek FPS dengan cara menghitung frame
                count_cmd = [
                    'ffprobe',
                    '-v', 'error',
                    '-count_frames',
                    '-select_streams', 'v:0',
                    '-show_entries', 'stream=nb_read_frames,duration',
                    '-of', 'csv=p=0',
                    input_path
                ]
                
                count_result = subprocess.run(count_cmd, capture_output=True, text=True, timeout=10)
                if count_result.returncode == 0 and count_result.stdout.strip():
                    parts = count_result.stdout.strip().split(',')
                    if len(parts) >= 2:
                        frames = float(parts[0]) if parts[0] and parts[0] != 'N/A' else 0
                        duration = float(parts[1]) if parts[1] and parts[1] != 'N/A' else 0
                        
                        if frames > 0 and duration > 0:
                            calculated_fps = frames / duration
                            if calculated_fps > 5:  # Hanya percaya jika > 5 fps
                                original_fps = calculated_fps
                                print(f"Calculated FPS from frame count: {original_fps:.2f}")
            except:
                pass
        
        # Jika masih aneh, gunakan default 30 fps untuk video modern
        if original_fps < 10:
            print(f"FPS still too low ({original_fps:.2f}), using default 30 fps")
            original_fps = 30
        
        # Dapatkan durasi
        try:
            duration_cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                input_path
            ]
            
            duration_result = subprocess.run(duration_cmd, capture_output=True, text=True, timeout=10)
            if duration_result.returncode == 0 and duration_result.stdout.strip():
                duration = float(duration_result.stdout.strip())
            else:
                duration = 15  # Default
        except:
            duration = 15
        
        print(f"Final video info: duration={duration:.2f}s, fps={original_fps:.2f}")
        
        # BATASI DURASI MAKSIMAL 60 DETIK
        max_duration = 60
        if duration > max_duration:
            duration = max_duration
        
        # TARGET: 12 FRAME PER DETIK (12 FPS)
        # TAPI untuk video pendek (< 2 detik), gunakan minimal 10 fps untuk smoothness
        if duration < 2:
            target_fps = 10
        else:
            target_fps = 12
        
        # Gunakan fps yang lebih rendah antara target dan original
        actual_fps = min(target_fps, original_fps)
        
        # Untuk video sangat pendek (< 1 detik), pastikan minimal 5 frame
        if duration < 1:
            min_frames = 5
            required_fps = min_frames / duration
            actual_fps = max(actual_fps, required_fps)
        
        # Batasi maksimal 30 fps untuk performa
        actual_fps = min(actual_fps, 30)
        
        print(f"Using {actual_fps:.1f} fps for conversion")
        
        # Hitung resolusi (dinamis berdasarkan durasi)
        if duration > 30:
            max_width = 400  # Lebih kecil untuk video panjang
        elif duration > 10:
            max_width = 480
        else:
            max_width = 560  # Lebih besar untuk video pendek
        
        scale_filter = f"scale={max_width}:-1:flags=lanczos"
        
        # METODE 1: Untuk video pendek (< 5 detik), gunakan metode simple dulu
        if duration < 5:
            print("Short video detected, using simple method...")
            
            simple_cmd = [
                'ffmpeg',
                '-i', input_path,
                '-t', str(duration),
                '-vf', f"fps={actual_fps:.1f},{scale_filter}",
                '-loop', '0',
                '-gifflags', '+offsetting',
                '-y', output_path
            ]
            
            print(f"Running: {' '.join(simple_cmd)}")
            result = subprocess.run(simple_cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0 and os.path.exists(output_path):
                # Cek apakah GIF memiliki frame
                check_cmd = [
                    'ffprobe',
                    '-v', 'error',
                    '-count_frames',
                    '-select_streams', 'v:0',
                    '-show_entries', 'stream=nb_read_frames',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    output_path
                ]
                
                check_result = subprocess.run(check_cmd, capture_output=True, text=True)
                if check_result.returncode == 0 and check_result.stdout.strip():
                    frames = int(check_result.stdout.strip())
                    print(f"GIF created with {frames} frames")
                    
                    if frames > 1:
                        return
                    else:
                        print("Only 1 frame created, trying palette method...")
        
        # METODE 2: Palette method (lebih baik untuk kualitas)
        print("Using palette method for better quality...")
        
        # Buat palette dulu
        palette_path = output_path + "_palette.png"
        
        try:
            palette_cmd = [
                'ffmpeg',
                '-i', input_path,
                '-t', str(duration),
                '-vf', f"fps={actual_fps:.1f},{scale_filter},palettegen=max_colors=128:stats_mode=diff",
                '-y', palette_path
            ]
            
            print("Generating palette...")
            subprocess.run(palette_cmd, capture_output=True, timeout=60)
            
            if not os.path.exists(palette_path):
                raise Exception("Failed to generate palette")
            
            # Buat GIF dengan palette
            gif_cmd = [
                'ffmpeg',
                '-i', input_path,
                '-i', palette_path,
                '-t', str(duration),
                '-lavfi', f"fps={actual_fps:.1f},{scale_filter}[x];[x][1:v]paletteuse=dither=none",
                '-loop', '0',
                '-gifflags', '+offsetting',
                '-y', output_path
            ]
            
            print("Creating GIF with palette...")
            result = subprocess.run(gif_cmd, capture_output=True, text=True, timeout=90)
            
        finally:
            # Cleanup palette
            if os.path.exists(palette_path):
                try:
                    os.remove(palette_path)
                except:
                    pass
        
        # Verifikasi hasil
        if not os.path.exists(output_path):
            raise Exception("Failed to create GIF file")
        
        # Cek frame count final
        final_check_cmd = [
            'ffprobe',
            '-v', 'error',
            '-count_frames',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=nb_read_frames',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            output_path
        ]
        
        final_result = subprocess.run(final_check_cmd, capture_output=True, text=True)
        if final_result.returncode == 0 and final_result.stdout.strip():
            frames = int(final_result.stdout.strip())
            print(f"Final GIF: {frames} frames")
            
            if frames <= 1:
                # Last resort: force minimum frames
                print("Forcing minimum frames...")
                force_cmd = [
                    'ffmpeg',
                    '-i', input_path,
                    '-vf', f"fps=10,{scale_filter}",  # Force 10 fps
                    '-frames:v', '10',  # Force 10 frames
                    '-loop', '0',
                    '-y', output_path
                ]
                subprocess.run(force_cmd, capture_output=True, timeout=60)
            
    except subprocess.TimeoutExpired:
        raise Exception("Konversi timeout. Coba video yang lebih pendek.")
    except Exception as e:
        raise Exception(f"Video conversion error: {str(e)}")
    
# ============================
# SIMPLE CONVERSION FALLBACK
# ============================

async def convert_image_simple_fallback(input_path, output_path):
    """Simple image to GIF conversion"""
    import subprocess
    
    try:
        cmd = [
            'ffmpeg', '-i', input_path,
            '-vf', 'format=rgb24',
            '-y', output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            raise Exception(f"Simple conversion failed: {result.stderr}")
            
    except Exception as e:
        raise Exception(f"Simple conversion error: {str(e)}")
    
# ============================
# WAIFU SYSTEM COMMANDS
# ============================

@bot.command(name="claim")
async def claim_waifu(ctx, *, character_name: str = None):
    """Claim daily waifu dengan opsi force character untuk admin
    
    Usage:
    !claim - Claim random waifu
    !claim naseshi - Admin bisa force claim karakter tertentu
    """
    await handle_waifu_claim(ctx, character_name)


# TAMBAHKAN COMMAND UNTUK CEK KARAKTER YANG ADA
@bot.command(name="waifulist")
async def waifu_list(ctx, *, search: str = None):
    """List semua waifu yang tersedia, bisa filter dengan nama"""
    waifu_folder = "./images/waifu"
    
    if not os.path.exists(waifu_folder):
        await ctx.send("📁 Folder waifu tidak ditemukan!")
        return
    
    waifus = [f for f in os.listdir(waifu_folder) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    
    if not waifus:
        await ctx.send("⚠️ Tidak ada gambar waifu di folder.")
        return
    
    # Filter jika ada search term
    if search:
        search_lower = search.lower()
        filtered_waifus = []
        for waifu_file in waifus:
            waifu_name = os.path.splitext(waifu_file)[0].lower()
            if search_lower in waifu_name:
                filtered_waifus.append(waifu_file)
        
        if not filtered_waifus:
            await ctx.send(f"❌ Tidak ditemukan waifu dengan nama **{search}**")
            return
        
        waifus = filtered_waifus
    
    # Format list
    waifu_names = []
    for waifu_file in waifus[:50]:  # Limit 50 untuk menghindari message terlalu panjang
        waifu_name = os.path.splitext(waifu_file)[0].replace("_", " ").title()
        waifu_names.append(f"• {waifu_name}")
    
    embed = discord.Embed(
        title="📋 List Waifu Tersedia" + (f" (Filter: {search})" if search else ""),
        description="\n".join(waifu_names),
        color=0xff69b4
    )
    
    if len(waifus) > 50:
        embed.set_footer(text=f"Menampilkan 50 dari {len(waifus)} waifu")
    
    await ctx.send(embed=embed)

@bot.command(name="resetclaim")
async def reset_claim_user(ctx, member: discord.Member = None):
    """Reset user's daily claim"""
    ADMIN_ID = 869897744972668948
    claim_file = "claimed_waifus.json"

    if ctx.author.id != ADMIN_ID:
        await ctx.send("🚫 Kamu tidak punya izin untuk menggunakan command ini.")
        return

    if member is None:
        await ctx.send("⚠️ Tag user yang ingin kamu reset, contoh: `n.resetclaim @user`")
        return

    if not os.path.exists(claim_file):
        await ctx.send("📁 File claim belum ada.")
        return

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

    waifu_name = data[user_id]["waifu"]
    data[user_id]["date"] = ""
    data[user_id]["waifu"] = waifu_name
    data[user_id]["count"] = data[user_id].get("count", 0)

    with open(claim_file, "w") as f:
        json.dump(data, f, indent=4)

    await ctx.send(f"🔁 Claim harian {member.mention} telah direset. Sekarang dia bisa claim lagi hari ini 💞")

@bot.command(name="topkarbit")
async def top_karbit(ctx):
    """Show waifu claim leaderboard"""
    await get_top_karbit(ctx)

# ============================
# BAN SYSTEM FUNCTIONS
# ============================

BOT_BANS_FILE = "bot_bans.json"

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
        return {}

def save_bans(data):
    with open(BOT_BANS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def is_timeout_expired(entry):
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
            del data[uid]
            changed = True
    if changed:
        save_bans(data)

@bot.check
async def global_not_banned_check(ctx):
    """Global ban check for all commands"""
    cleanup_expired_timeouts()
    data = load_bans()
    user_id = str(ctx.author.id)
    entry = data.get(user_id)
    if not entry:
        return True

    if entry.get("type") == "ban":
        await ctx.send(f"🚫 Maaf {ctx.author.mention}, kamu diblokir dari menggunakan bot ini. Alasan: {entry.get('reason','-')}")
        return False

    if entry.get("type") == "timeout":
        if is_timeout_expired(entry):
            del data[user_id]
            save_bans(data)
            return True
        else:
            until = entry.get("until")
            await ctx.send(f"⏳ Maaf {ctx.author.mention}, akses bot dibatasi sampai **{until} UTC**. Alasan: {entry.get('reason','-')}")
            return False

    return True

@bot.command(name="botban")
@commands.has_permissions(administrator=True)
async def bot_ban(ctx, member: discord.Member, *, reason: str = "Tidak disebutkan"):
    """Ban user from using bot"""
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
    """Unban user from bot"""
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
    """Timeout user from bot"""
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
        "until": until_dt.isoformat()
    }
    save_bans(data)
    await ctx.send(f"⏳ {member.mention} dibatasi akses bot sampai **{until_dt.isoformat()} UTC**. Alasan: {reason}")

@bot.command(name="botbanlist")
@commands.has_permissions(administrator=True)
async def bot_ban_list(ctx):
    """Show bot ban list"""
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

    embed = discord.Embed(title="🔒 Bot Ban List", description="\n".join(lines[:20]))
    await ctx.send(embed=embed)

# ============================
# HELP COMMAND
# ============================

@bot.command(aliases=['h', 'commands'])
async def help(ctx, category: str = None):
    """Show all commands organized by categories"""
    
    bot_avatar = bot.user.avatar.url if bot.user.avatar else bot.user.default_avatar.url
    github_avatar = "https://avatars.githubusercontent.com/u/117148725"
    creator_name = "nakzuwu"
    creator_url = "https://github.com/nakzuwu"
    
    # Update categories dictionary untuk menambahkan semua command
    categories = {
        "music": {
            "name": "🎵 Music Commands",
            "description": "Commands for music playback and queue management",
            "emoji": "🎵",
            "commands": {
                "play / p": "Play music from YouTube",
                "skip / s": "Skip current song", 
                "queue / q": "Show music queue",
                "loop": "Toggle loop current song",
                "loopqueue": "Toggle queue looping",
                "remove / rm": "Remove song from queue",
                "clear / c": "Clear queue",
                "volume / vol": "Set volume (0-100)",
                "shuffle": "Shuffle queue",
                "move": "Move song in queue",
                "stop / leave": "Stop music & disconnect",
                "lyrics / l": "Get lyrics for current/specific song"
            }
        },
        "download": {
            "name": "📥 Download Commands", 
            "description": "Commands for downloading media from various platforms",
            "emoji": "📥",
            "commands": {
                "yt": "Download YouTube video",
                "ytmp3": "Download YouTube audio (MP3)",
                "fb": "Download Facebook video", 
                "ig": "Download Instagram video",
                "twitter": "Download Twitter/X video",
                "ytthumbnail": "Get YouTube thumbnail",
                "togif": "Convert image/video to GIF"
            }
        },
        "anime": {
            "name": "🎌 Anime Commands",
            "description": "Anime information from MyAnimeList",
            "emoji": "🎌",
            "commands": {
                "seasonal": "Anime sedang tayang musim ini",
                "anime": "Cari anime + link MyAnimeList",
                "topanime": "Top anime dari MyAnimeList",
                "animeinfo": "Info detail lengkap anime", 
                "upcoming": "Anime yang akan datang musim depan",
                "character / char": "Cari karakter anime + anime asal",
                "va / seiyuu": "Cari voice actor/aktris",
                "compareva": "Bandingkan voice actor dua karakter"
            }
        },
        "waifu": {
            "name": "💖 Waifu System",
            "description": "Waifu claiming and management commands",
            "emoji": "💖",
            "commands": {
                "claim": "Claim daily waifu",
                "topkarbit": "Top waifu claimers leaderboard",
                "resetclaim": "[ADMIN] Reset user's daily claim"
            }
        },
        "utility": {
            "name": "🔧 Utility Commands",
            "description": "Various utility and admin commands",
            "emoji": "🔧", 
            "commands": {
                "help": "Show this help menu",
                "botban": "[ADMIN] Ban user from bot",
                "botunban": "[ADMIN] Unban user from bot",
                "bottimeout": "[ADMIN] Timeout user from bot", 
                "botbanlist": "[ADMIN] Show banned users"
            }
        }
    }

    if category and category.lower() in categories:
        cat_key = category.lower()
        cat_info = categories[cat_key]
        
        embed = discord.Embed(
            title=f"{cat_info['emoji']} {cat_info['name']}",
            description=cat_info['description'],
            color=0x00ff00
        )
        
        for cmd_name, cmd_desc in cat_info['commands'].items():
            embed.add_field(
                name=f"`{ctx.prefix}{cmd_name}`",
                value=cmd_desc,
                inline=False
            )
            
        embed.set_footer(text=f"Use {ctx.prefix}help for all categories")
        await ctx.send(embed=embed)
        return

    # Main help embed (semua kategori)
    embed = discord.Embed(
        title="REIKA BOT",
        description="Multi-purpose Discord bot dengan fitur lengkap!",
        color=0x00ff00,
        url="https://github.com/nakzuwu"
    )
    
    embed.set_author(name="Reika Bot", icon_url=bot_avatar)
    embed.set_thumbnail(url=github_avatar)
    
    # Bot info section
    embed.add_field(
        name="👨‍💻 Creator",
        value=f"[{creator_name}]({creator_url})",
        inline=True
    )
    
    embed.add_field(
        name="🔧 Bot Info", 
        value=f"Prefix: `{ctx.prefix}`\nCommands: {len(bot.commands)}",
        inline=True
    )
    
    embed.add_field(
        name="📊 Stats",
        value=f"Servers: {len(bot.guilds)}\nPing: {round(bot.latency * 1000)}ms",
        inline=True
    )
    
    # Category listing dengan semua kategori
    for cat_key, cat_info in categories.items():
        command_count = len(cat_info['commands'])
        example_commands = list(cat_info['commands'].keys())[:2]
        
        # Format contoh commands
        examples = []
        for cmd in example_commands:
            if ' / ' in cmd:
                # Jika ada alias, ambil yang pertama
                examples.append(f"`{ctx.prefix}{cmd.split(' / ')[0]}`")
            else:
                examples.append(f"`{ctx.prefix}{cmd}`")
        
        example_text = ", ".join(examples)
        
        embed.add_field(
            name=f"{cat_info['emoji']} {cat_info['name']} ({command_count} commands)",
            value=f"{cat_info['description']}\nExamples: {example_text}",
            inline=False
        )

    # Tips section
    embed.add_field(
        name="💡 Usage Tips",
        value=(
            f"• Use `{ctx.prefix}help <category>` untuk perintah spesifik\n"
            f"• Music commands memiliki short aliases (p, s, q, etc)\n"
            f"• Anime commands mengambil data langsung dari MyAnimeList\n"
            f"• Case-insensitive commands & prefix\n"
            f"• Gunakan quotes untuk search multi-kata: `{ctx.prefix}anime \"naruto shippuden\"`"
        ),
        inline=False
    )
    
    # Footer
    embed.set_footer(
        text="Powered by Discord.py • Made with 💖 by nakzuwu",
        icon_url=github_avatar
    )

    await ctx.send(embed=embed)

@help.error
async def help_error(ctx, error):
    """Error handler for help command"""
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ Category not found. Use `help` to see available categories.")

# ============================
# MAL COMMANDS COG
# ============================

class MALCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.base_url = "https://api.jikan.moe/v4"

    # ============================
    # EXISTING COMMANDS - IMPROVED
    # ============================

    @commands.command(name='seasonal')
    async def seasonal_anime(self, ctx, limit: int = 15):
        """Menampilkan anime yang sedang tayang musim ini dengan detail"""
        await ctx.send("🎌 Mengambil data anime seasonal dari MyAnimeList...")
        
        try:
            url = f"{self.base_url}/seasons/now"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        anime_list = data['data'][:limit]
                        
                        # Buat embed yang lebih informatif
                        embed = discord.Embed(
                            title="🍿 Anime Sedang Tayang (Musim Ini)",
                            description=f"**{len(anime_list)} anime** yang sedang tayang musim ini\n*Gunakan `{ctx.prefix}anime <judul>` untuk info detail*",
                            color=0x2e51a2,
                            url="https://myanimelist.net/anime/season"
                        )
                        
                        for i, anime in enumerate(anime_list, 1):
                            title = anime['title']
                            mal_url = anime['url']
                            episodes = anime['episodes'] or "TBA"
                            score = anime['score'] or "Belum ada"
                            status = anime['status']
                            
                            # Info tambahan
                            genres = [genre['name'] for genre in anime.get('genres', [])[:3]]
                            genres_text = ", ".join(genres) if genres else "TBA"
                            
                            studios = [studio['name'] for studio in anime.get('studios', [])[:2]]
                            studios_text = ", ".join(studios) if studios else "TBA"
                            
                            # Format field value
                            field_value = (
                                f"**Score:** ⭐ {score}\n"
                                f"**Episodes:** {episodes} | **Status:** {status}\n"
                                f"**Genres:** {genres_text}\n"
                                f"**Studio:** {studios_text}\n"
                                f"🔗 [MyAnimeList]({mal_url})"
                            )
                            
                            embed.add_field(
                                name=f"#{i} {title}",
                                value=field_value,
                                inline=False
                            )
                            
                            # Set thumbnail untuk anime pertama
                            if i == 1 and anime.get('images'):
                                thumbnail = anime['images']['jpg']['image_url']
                                embed.set_thumbnail(url=thumbnail)
                        
                        embed.set_footer(text=f"Gunakan {ctx.prefix}anime <judul> untuk info detail • Powered by Jikan API")
                        await ctx.send(embed=embed)
                    else:
                        await ctx.send("❌ Gagal mengambil data seasonal anime")
                        
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='anime')
    async def search_anime(self, ctx, *, query):
        """Mencari anime dengan rekomendasi dan info detail lengkap"""
        await ctx.send(f"🔍 Mencari anime: **{query}**")
        
        try:
            # Search dengan limit lebih banyak untuk rekomendasi
            url = f"{self.base_url}/anime?q={query}&limit=5"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if not data['data']:
                            await ctx.send("❌ Anime tidak ditemukan di MyAnimeList")
                            return
                        
                        # Ambil anime pertama sebagai hasil utama
                        main_anime = data['data'][0]
                        
                        # Ambil rekomendasi (anime lainnya)
                        recommendations = data['data'][1:4]  # 3 rekomendasi
                        
                        # Ambil detail lengkap untuk anime utama
                        anime_id = main_anime['mal_id']
                        detail_url = f"{self.base_url}/anime/{anime_id}/full"
                        
                        async with session.get(detail_url) as detail_response:
                            if detail_response.status == 200:
                                full_data = await detail_response.json()
                                anime = full_data['data']
                                
                                # Buat embed utama yang sangat detail
                                embed = self._create_detailed_anime_embed(anime, ctx)
                                await ctx.send(embed=embed)
                                
                                # Kirim rekomendasi sebagai embed terpisah
                                if recommendations:
                                    await self._send_recommendations(ctx, recommendations, query)
                                    
                            else:
                                # Fallback ke basic info jika detail gagal
                                await self._send_basic_anime_info(ctx, main_anime)
                        
                    else:
                        await ctx.send("❌ Gagal mencari anime di MyAnimeList")
                        
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='topanime')
    async def top_anime(self, ctx, limit: int = 15):
        """Menampilkan top anime dengan detail"""
        await ctx.send("🏆 Mengambil top anime dari MyAnimeList...")
        
        try:
            url = f"{self.base_url}/top/anime"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        anime_list = data['data'][:limit]
                        
                        embed = discord.Embed(
                            title="🏅 Top Anime MyAnimeList",
                            description=f"**Top {len(anime_list)} anime** terbaik semua waktu\n*Gunakan `{ctx.prefix}anime <judul>` untuk info detail*",
                            color=0xffd700,
                            url="https://myanimelist.net/topanime.php"
                        )
                        
                        for i, anime in enumerate(anime_list, 1):
                            title = anime['title']
                            mal_url = anime['url']
                            score = anime['score'] or "N/A"
                            episodes = anime['episodes'] or "TBA"
                            rank = anime.get('rank', 'N/A')
                            members = f"{anime.get('members', 0):,}" if anime.get('members') else "N/A"
                            
                            # Info genres
                            genres = [genre['name'] for genre in anime.get('genres', [])[:2]]
                            genres_text = ", ".join(genres) if genres else "Various"
                            
                            embed.add_field(
                                name=f"#{rank} {title}",
                                value=(
                                    f"⭐ **{score}** | 📺 **{episodes}** eps\n"
                                    f"🎭 **{genres_text}**\n"
                                    f"👥 **{members}** members\n"
                                    f"🔗 [MAL]({mal_url})"
                                ),
                                inline=True
                            )
                            
                            # Set thumbnail untuk anime pertama
                            if i == 1 and anime.get('images'):
                                thumbnail = anime['images']['jpg']['image_url']
                                embed.set_thumbnail(url=thumbnail)
                        
                        embed.set_footer(text="Data dari MyAnimeList Top Anime")
                        await ctx.send(embed=embed)
                    else:
                        await ctx.send("❌ Gagal mengambil top anime")
                        
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='animeinfo')
    async def anime_detail(self, ctx, *, query):
        """Info super detail anime dari MyAnimeList"""
        await ctx.send(f"📖 Mengambil info detail lengkap anime: **{query}**")
        
        try:
            search_url = f"{self.base_url}/anime?q={query}&limit=1"
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url) as response:
                    if response.status != 200:
                        await ctx.send("❌ Gagal mencari anime")
                        return
                    
                    search_data = await response.json()
                    if not search_data['data']:
                        await ctx.send("❌ Anime tidak ditemukan")
                        return
                    
                    anime_data = search_data['data'][0]
                    anime_id = anime_data['mal_id']
                    
                    # Ambil data lengkap
                    detail_url = f"{self.base_url}/anime/{anime_id}/full"
                    async with session.get(detail_url) as detail_response:
                        if detail_response.status == 200:
                            full_data = await detail_response.json()
                            anime = full_data['data']
                            
                            # Buat embed super detail
                            embed = self._create_super_detailed_anime_embed(anime, ctx)
                            await ctx.send(embed=embed)
                            
                            # Kirim info relationships (sequel, prequel, etc)
                            await self._send_anime_relationships(ctx, anime)
                            
                            # Kirim info characters
                            await self._send_anime_characters(ctx, anime_id)
                            
                        else:
                            await ctx.send("❌ Gagal mengambil detail anime")
                            
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='upcoming')
    async def upcoming_anime(self, ctx, limit: int = 12):
        """Anime yang akan datang musim depan dengan detail"""
        try:
            # Tentukan musim berikutnya
            now = datetime.now()
            year = now.year
            month = now.month
            
            if month <= 3:
                next_season = "spring"
            elif month <= 6:
                next_season = "summer"
            elif month <= 9:
                next_season = "fall"
            else:
                next_season = "winter"
                year += 1
            
            url = f"{self.base_url}/seasons/{year}/{next_season}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        anime_list = data['data'][:limit]
                        
                        embed = discord.Embed(
                            title=f"🎬 Upcoming Anime ({next_season.capitalize()} {year})",
                            description=f"**{len(anime_list)} anime** yang akan tayang musim depan\n*Gunakan `{ctx.prefix}anime <judul>` untuk info detail*",
                            color=0x00ff00,
                            url=f"https://myanimelist.net/anime/season/{year}/{next_season}"
                        )
                        
                        for anime in anime_list:
                            title = anime['title']
                            mal_url = anime['url']
                            episodes = anime.get('episodes', 'TBA')
                            score = anime.get('score', 'Not rated')
                            anime_type = anime.get('type', 'Unknown')
                            
                            # Info genres
                            genres = [genre['name'] for genre in anime.get('genres', [])[:2]]
                            genres_text = ", ".join(genres) if genres else "TBA"
                            
                            embed.add_field(
                                name=title,
                                value=(
                                    f"**Type:** {anime_type} | **Episodes:** {episodes}\n"
                                    f"**Genres:** {genres_text}\n"
                                    f"⭐ **Score:** {score}\n"
                                    f"🔗 [MAL]({mal_url})"
                                ),
                                inline=True
                            )
                        
                        embed.set_footer(text=f"MyAnimeList {next_season.capitalize()} {year} • {len(anime_list)} anime")
                        await ctx.send(embed=embed)
                    else:
                        await ctx.send("❌ Gagal mengambil data upcoming anime")
                        
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

    # ============================
    # NEW CHARACTER COMMANDS
    # ============================

    @commands.command(name='character', aliases=['char'])
    async def search_character(self, ctx, *, query):
        """Mencari karakter anime dan info detailnya dengan anime asal"""
        await ctx.send(f"👤 Mencari karakter: **{query}**")
        
        try:
            url = f"{self.base_url}/characters?q={query}&limit=10"  # Limit lebih banyak untuk akurasi
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if not data['data']:
                            await ctx.send("❌ Karakter tidak ditemukan")
                            return
                        
                        # Cari karakter yang paling tepat match dengan query
                        exact_match = None
                        partial_matches = []
                        
                        for char in data['data']:
                            char_name = char['name'].lower()
                            query_lower = query.lower()
                            
                            # Exact match
                            if char_name == query_lower:
                                exact_match = char
                                break
                            # Partial match (contains)
                            elif query_lower in char_name:
                                partial_matches.append(char)
                        
                        # Prioritize exact match, then partial matches
                        if exact_match:
                            target_char = exact_match
                        elif partial_matches:
                            target_char = partial_matches[0]
                        else:
                            target_char = data['data'][0]  # Fallback ke pertama
                        
                        # Jika ada multiple matches, kirim pilihan
                        if len(data['data']) > 1 and not exact_match:
                            await self._send_character_choices(ctx, data['data'][:5], query)
                        
                        char_id = target_char['mal_id']
                        
                        # Ambil detail lengkap karakter
                        detail_url = f"{self.base_url}/characters/{char_id}/full"
                        async with session.get(detail_url) as detail_response:
                            if detail_response.status == 200:
                                char_data = await detail_response.json()
                                character = char_data['data']
                                
                                await self._send_character_details(ctx, character)
                                
                            else:
                                await self._send_basic_character_info(ctx, target_char)
                        
                    else:
                        await ctx.send("❌ Gagal mencari karakter")
                        
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

    async def _send_character_choices(self, ctx, characters, original_query):
        """Kirim pilihan karakter jika ada multiple matches"""
        embed = discord.Embed(
            title="🔍 Multiple Characters Found",
            description=f"Beberapa karakter ditemukan untuk **{original_query}**. Menggunakan hasil terbaik.\n\n**Pilihan lainnya:**",
            color=0xffa500
        )
        
        for i, char in enumerate(characters[1:4], 1):  # Tampilkan 3 pilihan lainnya
            char_name = char['name']
            char_url = char['url']
            
            # Coba dapatkan anime asal dari API
            anime_origin = "Unknown"
            try:
                char_id = char['mal_id']
                async with aiohttp.ClientSession() as session:
                    detail_url = f"{self.base_url}/characters/{char_id}"
                    async with session.get(detail_url) as response:
                        if response.status == 200:
                            char_data = await response.json()
                            anime_list = char_data['data'].get('anime', [])
                            if anime_list:
                                anime_origin = anime_list[0]['anime']['name']
            except:
                pass
            
            embed.add_field(
                name=f"{i}. {char_name}",
                value=f"**Anime:** {anime_origin}\n[MyAnimeList]({char_url})",
                inline=False
            )
        
        embed.set_footer(text="Gunakan !character <nama lengkap> untuk hasil yang lebih spesifik")
        await ctx.send(embed=embed)

    @commands.command(name='va', aliases=['seiyuu', 'voiceactor'])
    async def search_voice_actor(self, ctx, *, query):
        """Mencari voice actor/aktris pengisi suara"""
        await ctx.send(f"🎙️ Mencari voice actor: **{query}**")
        
        try:
            url = f"{self.base_url}/people?q={query}&limit=5"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if not data['data']:
                            await ctx.send("❌ Voice actor tidak ditemukan")
                            return
                        
                        # Ambil voice actor utama
                        main_va = data['data'][0]
                        va_id = main_va['mal_id']
                        
                        # Ambil detail lengkap
                        detail_url = f"{self.base_url}/people/{va_id}/full"
                        async with session.get(detail_url) as detail_response:
                            if detail_response.status == 200:
                                va_data = await detail_response.json()
                                voice_actor = va_data['data']
                                
                                await self._send_voice_actor_details(ctx, voice_actor)
                                
                            else:
                                await self._send_basic_voice_actor_info(ctx, main_va)
                        
                    else:
                        await ctx.send("❌ Gagal mencari voice actor")
                        
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.command(name='compareva')
    async def compare_voice_actors(self, ctx, *, characters):
        """Membandingkan voice actor dua karakter dengan identifikasi yang jelas"""
        try:
            # Improved parsing untuk handle berbagai format
            if ' vs ' in characters:
                char_list = characters.split(' vs ')
            elif ' VS ' in characters:
                char_list = characters.split(' VS ')
            else:
                await ctx.send("❌ Format: `!compareva <karakter1> vs <karakter2>`\nContoh: `!compareva \"Tendou Alice\" vs \"Nakano Azusa\"`")
                return
            
            if len(char_list) != 2:
                await ctx.send("❌ Format: `!compareva <karakter1> vs <karakter2>`")
                return
            
            char1_query, char2_query = [q.strip() for q in char_list]
            await ctx.send(f"🔍 Membandingkan VA: **{char1_query}** 🆚 **{char2_query}**")
            
            # Cari kedua karakter dengan matching yang lebih baik
            char1_data, char1_full = await self._find_character_with_anime(char1_query)
            char2_data, char2_full = await self._find_character_with_anime(char2_query)
            
            if not char1_data:
                await ctx.send(f"❌ Karakter tidak ditemukan: **{char1_query}**")
                return
            if not char2_data:
                await ctx.send(f"❌ Karakter tidak ditemukan: **{char2_query}**")
                return
            
            await self._send_voice_actor_comparison(ctx, char1_full, char2_full, char1_query, char2_query)
                        
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")

    async def _find_character_with_anime(self, query):
        """Mencari karakter dengan info anime yang jelas"""
        try:
            url = f"{self.base_url}/characters?q={query}&limit=5"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None, None
                    
                    data = await response.json()
                    if not data['data']:
                        return None, None
                    
                    # Cari exact match dulu
                    exact_match = None
                    for char in data['data']:
                        if char['name'].lower() == query.lower():
                            exact_match = char
                            break
                    
                    # Jika tidak ada exact match, gunakan yang pertama
                    target_char = exact_match if exact_match else data['data'][0]
                    
                    # Ambil detail lengkap
                    char_id = target_char['mal_id']
                    detail_url = f"{self.base_url}/characters/{char_id}/full"
                    async with session.get(detail_url) as detail_response:
                        if detail_response.status == 200:
                            char_data = await detail_response.json()
                            return target_char, char_data['data']
                        else:
                            return target_char, None
                            
        except Exception as e:
            print(f"Error finding character: {e}")
            return None, None

    # ============================
    # HELPER METHODS
    # ============================

    def _create_detailed_anime_embed(self, anime, ctx):
        """Membuat embed detail untuk anime"""
        embed = discord.Embed(
            title=f"🎌 {anime['title']}",
            url=anime['url'],
            color=0x2e51a2
        )
        
        # Basic info
        score = anime.get('score', 'N/A')
        rank = f"#{anime['rank']}" if anime.get('rank') else "N/A"
        popularity = f"#{anime['popularity']}" if anime.get('popularity') else "N/A"
        
        embed.add_field(name="⭐ Score", value=score, inline=True)
        embed.add_field(name="🏆 Rank", value=rank, inline=True)
        embed.add_field(name="👥 Popularity", value=popularity, inline=True)
        
        # Episode info
        episodes = anime.get('episodes', 'TBA')
        status = anime.get('status', 'Unknown')
        anime_type = anime.get('type', 'Unknown')
        
        embed.add_field(name="📺 Episodes", value=episodes, inline=True)
        embed.add_field(name="📅 Status", value=status, inline=True)
        embed.add_field(name="🎬 Type", value=anime_type, inline=True)
        
        # Studios & Genres
        studios = [s['name'] for s in anime.get('studios', [])]
        genres = [g['name'] for g in anime.get('genres', [])]
        
        embed.add_field(name="🏢 Studios", value=", ".join(studios) if studios else "Unknown", inline=True)
        embed.add_field(name="🎭 Genres", value=", ".join(genres[:5]) if genres else "Unknown", inline=True)
        
        # Aired info
        aired_info = "Unknown"
        if anime.get('aired') and anime['aired'].get('string'):
            aired_info = anime['aired']['string']
        embed.add_field(name="📆 Aired", value=aired_info, inline=True)
        
        # Synopsis
        synopsis = anime.get('synopsis') or "No synopsis available"
        if len(synopsis) > 800:
            synopsis = synopsis[:800] + "..."
        embed.add_field(name="📖 Synopsis", value=synopsis, inline=False)
        
        # Thumbnail
        if anime.get('images') and anime['images'].get('jpg'):
            thumbnail = anime['images']['jpg'].get('large_image_url')
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
        
        embed.set_footer(text=f"Gunakan {ctx.prefix}animeinfo untuk detail lengkap • MyAnimeList")
        return embed

    def _create_super_detailed_anime_embed(self, anime, ctx):
        """Membuat embed super detail untuk anime"""
        embed = discord.Embed(
            title=f"📚 {anime['title']}",
            url=anime['url'],
            description=anime.get('synopsis', 'No synopsis available')[:300] + "...",
            color=0x2e51a2
        )
        
        # Extended info
        embed.add_field(name="⭐ Score", value=anime.get('score', 'N/A'), inline=True)
        embed.add_field(name="🏆 Rank", value=f"#{anime['rank']}" if anime.get('rank') else "N/A", inline=True)
        embed.add_field(name="👥 Popularity", value=f"#{anime['popularity']}" if anime.get('popularity') else "N/A", inline=True)
        
        embed.add_field(name="📺 Episodes", value=anime.get('episodes', 'TBA'), inline=True)
        embed.add_field(name="📅 Status", value=anime.get('status', 'Unknown'), inline=True)
        embed.add_field(name="🎬 Type", value=anime.get('type', 'Unknown'), inline=True)
        
        # Duration and rating
        duration = anime.get('duration', 'Unknown')
        rating = anime.get('rating', 'Unknown')
        embed.add_field(name="⏱️ Duration", value=duration, inline=True)
        embed.add_field(name="🔞 Rating", value=rating, inline=True)
        
        # Studios & Producers
        studios = [s['name'] for s in anime.get('studios', [])]
        producers = [p['name'] for p in anime.get('producers', [])[:3]]
        
        embed.add_field(name="🏢 Studios", value=", ".join(studios) if studios else "Unknown", inline=True)
        embed.add_field(name="💰 Producers", value=", ".join(producers) if producers else "Unknown", inline=True)
        
        # Genres & Themes
        genres = [g['name'] for g in anime.get('genres', [])]
        themes = [t['name'] for t in anime.get('themes', [])[:3]]
        
        embed.add_field(name="🎭 Genres", value=", ".join(genres) if genres else "Unknown", inline=True)
        embed.add_field(name="🎪 Themes", value=", ".join(themes) if themes else "Unknown", inline=True)
        
        # Aired info
        aired_info = "Unknown"
        if anime.get('aired') and anime['aired'].get('string'):
            aired_info = anime['aired']['string']
        embed.add_field(name="📆 Aired", value=aired_info, inline=False)
        
        # Thumbnail
        if anime.get('images') and anime['images'].get('jpg'):
            thumbnail = anime['images']['jpg'].get('large_image_url')
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
        
        embed.set_footer(text="Lanjut ke message berikutnya untuk info relationships dan characters...")
        return embed

    async def _send_recommendations(self, ctx, recommendations, original_query):
        """Mengirim embed rekomendasi anime"""
        embed = discord.Embed(
            title="💡 Rekomendasi Anime Lainnya",
            description=f"Anime lain yang mirip dengan **{original_query}**",
            color=0x00ff00
        )
        
        for i, anime in enumerate(recommendations, 1):
            title = anime['title']
            mal_url = anime['url']
            score = anime.get('score', 'N/A')
            episodes = anime.get('episodes', 'TBA')
            
            embed.add_field(
                name=f"{i}. {title}",
                value=f"⭐ {score} | 📺 {episodes} eps | [MAL]({mal_url})",
                inline=False
            )
        
        await ctx.send(embed=embed)

    async def _send_anime_relationships(self, ctx, anime):
        """Mengirim info relationships (sequel, prequel, etc)"""
        relations = anime.get('relations', [])
        if not relations:
            return
        
        embed = discord.Embed(
            title="🔗 Related Anime",
            color=0x888888
        )
        
        for relation in relations[:6]:  # Batasi agar tidak terlalu panjang
            relation_type = relation.get('relation', 'Unknown')
            related_anime = relation.get('entry', [])
            
            if related_anime:
                anime_titles = [f"[{anime['name']}]({anime['url']})" for anime in related_anime[:3]]
                embed.add_field(
                    name=relation_type.replace('_', ' ').title(),
                    value=", ".join(anime_titles),
                    inline=True
                )
        
        if embed.fields:
            await ctx.send(embed=embed)

    async def _send_anime_characters(self, ctx, anime_id):
        """Mengirim info karakter utama"""
        try:
            url = f"{self.base_url}/anime/{anime_id}/characters"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        characters = data['data'][:8]  # Ambil 8 karakter utama
                        
                        if not characters:
                            return
                        
                        embed = discord.Embed(
                            title="👥 Karakter Utama",
                            color=0x3498db
                        )
                        
                        for char in characters:
                            char_name = char['character']['name']
                            char_url = char['character']['url']
                            va_name = char['voice_actors'][0]['person']['name'] if char.get('voice_actors') else "Unknown"
                            va_url = char['voice_actors'][0]['person']['url'] if char.get('voice_actors') else "#"
                            
                            embed.add_field(
                                name=char_name,
                                value=f"VA: [{va_name}]({va_url})",
                                inline=True
                            )
                        
                        await ctx.send(embed=embed)
                        
        except Exception as e:
            print(f"Error fetching characters: {e}")

    async def _send_character_details(self, ctx, character):
        embed = discord.Embed(
            title=f"👤 {character['name']}",
            url=character['url'],
            color=0x3498db
        )
        
        # Basic info
        if character.get('name_kanji'):
            embed.add_field(name="🈲 Nama Kanji", value=character['name_kanji'], inline=True)
        
        if character.get('favorites'):
            embed.add_field(name="❤️ Favorites", value=f"{character['favorites']:,}", inline=True)
        
        # Anime Origin - INI YANG BARU
        anime_origin = await self._get_character_anime_origin(character)
        if anime_origin:
            embed.add_field(name="🎬 Anime Asal", value=anime_origin, inline=True)
        
        # Nicknames
        nicknames = character.get('nicknames', [])
        if nicknames:
            embed.add_field(name="🏷️ Nama Panggilan", value=", ".join(nicknames[:3]), inline=True)
        
        # About
        about = character.get('about')
        if about and len(about) > 400:
            about = about[:400] + "..."
        if about:
            embed.add_field(name="📝 About", value=about, inline=False)
        
        # Thumbnail
        if character.get('images') and character['images'].get('jpg'):
            thumbnail = character['images']['jpg']['image_url']
            embed.set_thumbnail(url=thumbnail)
        
        # Voice actors dengan bahasa
        voice_actors = character.get('voices', [])
        if voice_actors:
            japanese_vas = [va for va in voice_actors if va.get('language', '').lower() == 'japanese']
            if japanese_vas:
                va_info = []
                for va in japanese_vas[:2]:  # Max 2 Japanese VA
                    person = va['person']
                    va_info.append(f"[{person['name']}]({person['url']})")
                
                embed.add_field(name="🎙️ Japanese Voice Actors", value="\n".join(va_info), inline=False)
        
        await ctx.send(embed=embed)
        
        # Kirim info anime appearances terpisah
        await self._send_character_anime_appearances(ctx, character)

    async def _get_character_anime_origin(self, character):
        """Mendapatkan anime asal karakter"""
        try:
            anime_appearances = character.get('anime', [])
            if anime_appearances:
                # Cari anime utama (biasanya yang pertama)
                main_anime = anime_appearances[0]['anime']
                return f"[{main_anime['name']}]({main_anime['url']})"
        except:
            pass
        return None

    async def _send_character_anime_appearances(self, ctx, character):
        """Mengirim daftar anime tempat karakter muncul"""
        anime_appearances = character.get('anime', [])
        if not anime_appearances:
            return
        
        embed = discord.Embed(
            title=f"🎬 Penampilan {character['name']}",
            color=0x9b59b6
        )
        
        # Group by role
        main_roles = []
        supporting_roles = []
        
        for appearance in anime_appearances[:8]:  # Limit 8 anime
            anime = appearance['anime']
            role = appearance.get('role', 'Supporting').title()
            
            anime_info = f"[{anime['name']}]({anime['url']}) ({role})"
            
            if role == 'Main':
                main_roles.append(anime_info)
            else:
                supporting_roles.append(anime_info)
        
        # Tampilkan main roles dulu
        if main_roles:
            embed.add_field(
                name="🌟 Peran Utama",
                value="\n".join(main_roles[:4]),
                inline=False
            )
        
        # Supporting roles
        if supporting_roles:
            embed.add_field(
                name="📺 Peran Pendukung",
                value="\n".join(supporting_roles[:4]),
                inline=False
            )
        
        if embed.fields:
            await ctx.send(embed=embed)    
            
    async def _send_character_details(self, ctx, character):
        """Mengirim detail karakter dengan info anime asal"""
        embed = discord.Embed(
            title=f"👤 {character['name']}",
            url=character['url'],
            color=0x3498db
        )
        
        # Basic info
        if character.get('name_kanji'):
            embed.add_field(name="🈲 Nama Kanji", value=character['name_kanji'], inline=True)
        
        if character.get('favorites'):
            embed.add_field(name="❤️ Favorites", value=f"{character['favorites']:,}", inline=True)
        
        # Anime Origin - INI YANG BARU
        anime_origin = await self._get_character_anime_origin(character)
        if anime_origin:
            embed.add_field(name="🎬 Anime Asal", value=anime_origin, inline=True)
        
        # Nicknames
        nicknames = character.get('nicknames', [])
        if nicknames:
            embed.add_field(name="🏷️ Nama Panggilan", value=", ".join(nicknames[:3]), inline=True)
        
        # About
        about = character.get('about')
        if about and len(about) > 400:
            about = about[:400] + "..."
        if about:
            embed.add_field(name="📝 About", value=about, inline=False)
        
        # Thumbnail
        if character.get('images') and character['images'].get('jpg'):
            thumbnail = character['images']['jpg']['image_url']
            embed.set_thumbnail(url=thumbnail)
        
        # Voice actors dengan bahasa
        voice_actors = character.get('voices', [])
        if voice_actors:
            japanese_vas = [va for va in voice_actors if va.get('language', '').lower() == 'japanese']
            if japanese_vas:
                va_info = []
                for va in japanese_vas[:2]:  # Max 2 Japanese VA
                    person = va['person']
                    va_info.append(f"[{person['name']}]({person['url']})")
                
                embed.add_field(name="🎙️ Japanese Voice Actors", value="\n".join(va_info), inline=False)
        
        await ctx.send(embed=embed)
        
        # Kirim info anime appearances terpisah
        await self._send_character_anime_appearances(ctx, character)

    async def _get_character_anime_origin(self, character):
        """Mendapatkan anime asal karakter"""
        try:
            anime_appearances = character.get('anime', [])
            if anime_appearances:
                # Cari anime utama (biasanya yang pertama)
                main_anime = anime_appearances[0]['anime']
                return f"[{main_anime['name']}]({main_anime['url']})"
        except:
            pass
        return None

    async def _send_character_anime_appearances(self, ctx, character):
        """Mengirim daftar anime tempat karakter muncul"""
        anime_appearances = character.get('anime', [])
        if not anime_appearances:
            return
        
        embed = discord.Embed(
            title=f"🎬 Penampilan {character['name']}",
            color=0x9b59b6
        )
        
        # Group by role
        main_roles = []
        supporting_roles = []
        
        for appearance in anime_appearances[:8]:  # Limit 8 anime
            anime = appearance['anime']
            role = appearance.get('role', 'Supporting').title()
            
            anime_info = f"[{anime['name']}]({anime['url']}) ({role})"
            
            if role == 'Main':
                main_roles.append(anime_info)
            else:
                supporting_roles.append(anime_info)
        
        # Tampilkan main roles dulu
        if main_roles:
            embed.add_field(
                name="🌟 Peran Utama",
                value="\n".join(main_roles[:4]),
                inline=False
            )
        
        # Supporting roles
        if supporting_roles:
            embed.add_field(
                name="📺 Peran Pendukung",
                value="\n".join(supporting_roles[:4]),
                inline=False
            )
        
        if embed.fields:
            await ctx.send(embed=embed)

    async def _send_voice_actor_details(self, ctx, voice_actor):
        """Mengirim detail voice actor"""
        embed = discord.Embed(
            title=f"🎙️ {voice_actor['name']}",
            url=voice_actor['url'],
            color=0xe74c3c
        )
        
        # Basic info
        if voice_actor.get('given_name'):
            embed.add_field(name="📛 Given Name", value=voice_actor['given_name'], inline=True)
        
        if voice_actor.get('family_name'):
            embed.add_field(name="👨‍👩‍👧‍👦 Family Name", value=voice_actor['family_name'], inline=True)
        
        if voice_actor.get('birthday'):
            embed.add_field(name="🎂 Birthday", value=voice_actor['birthday'], inline=True)
        
        # About
        about = voice_actor.get('about')
        if about and len(about) > 400:
            about = about[:400] + "..."
        if about:
            embed.add_field(name="📝 About", value=about, inline=False)
        
        # Thumbnail
        if voice_actor.get('images') and voice_actor['images'].get('jpg'):
            thumbnail = voice_actor['images']['jpg']['image_url']
            embed.set_thumbnail(url=thumbnail)
        
        # Popular roles (max 5)
        voices = voice_actor.get('voices', [])
        if voices:
            roles = []
            for voice in voices[:5]:
                character = voice['character']
                anime = voice['anime'][0] if voice.get('anime') else None
                if anime:
                    roles.append(f"**{character['name']}** in [{anime['name']}]({anime['url']})")
            
            if roles:
                embed.add_field(name="🎭 Popular Roles", value="\n".join(roles), inline=False)
        
        await ctx.send(embed=embed)

    async def _send_voice_actor_comparison(self, ctx, char1, char2, char1_query, char2_query):
        """Membandingkan voice actor dua karakter dengan info yang jelas"""
        embed = discord.Embed(
            title="🔊 Perbandingan Voice Actor",
            color=0x9b59b6
        )
        
        # Character 1 info dengan anime asal
        char1_anime_origin = await self._get_character_anime_origin(char1)
        char1_vas = char1.get('voices', [])
        char1_japanese_vas = [va for va in char1_vas if va.get('language', '').lower() == 'japanese']
        
        char1_info = f"**Anime:** {char1_anime_origin or 'Unknown'}\n"
        char1_info += "**Japanese VA:**\n"
        if char1_japanese_vas:
            for va in char1_japanese_vas[:2]:
                person = va['person']
                char1_info += f"• [{person['name']}]({person['url']})\n"
        else:
            char1_info += "• Not available\n"
        
        embed.add_field(
            name=f"👤 {char1['name']}",
            value=char1_info,
            inline=True
        )
        
        # Character 2 info dengan anime asal
        char2_anime_origin = await self._get_character_anime_origin(char2)
        char2_vas = char2.get('voices', [])
        char2_japanese_vas = [va for va in char2_vas if va.get('language', '').lower() == 'japanese']
        
        char2_info = f"**Anime:** {char2_anime_origin or 'Unknown'}\n"
        char2_info += "**Japanese VA:**\n"
        if char2_japanese_vas:
            for va in char2_japanese_vas[:2]:
                person = va['person']
                char2_info += f"• [{person['name']}]({person['url']})\n"
        else:
            char2_info += "• Not available\n"
        
        embed.add_field(
            name=f"👤 {char2['name']}",
            value=char2_info,
            inline=True
        )
        
        # Comparison result
        char1_va_ids = {va['person']['mal_id'] for va in char1_japanese_vas}
        char2_va_ids = {va['person']['mal_id'] for va in char2_japanese_vas}
        common_vas = char1_va_ids.intersection(char2_va_ids)
        
        if common_vas:
            common_va_names = []
            for va in char1_japanese_vas:
                if va['person']['mal_id'] in common_vas:
                    common_va_names.append(f"[{va['person']['name']}]({va['person']['url']})")
            
            embed.add_field(
                name="✅ Shared Voice Actors",
                value=", ".join(common_va_names),
                inline=False
            )
            
            # Info tambahan: di anime apa mereka bersama?
            shared_anime_info = await self._get_shared_anime_info(char1, char2, common_vas)
            if shared_anime_info:
                embed.add_field(
                    name="🎬 Bersama di Anime",
                    value=shared_anime_info,
                    inline=False
                )
        else:
            embed.add_field(
                name="❌ No Shared Japanese Voice Actors",
                value="Kedua karakter memiliki voice actor yang berbeda",
                inline=False
            )
        
        # Footer dengan query asli untuk konfirmasi
        embed.set_footer(text=f"Query: {char1_query} vs {char2_query}")
        await ctx.send(embed=embed)

    async def _get_shared_anime_info(self, char1, char2, common_vas):
        """Mendapatkan info anime dimana VA yang sama mengisi suara kedua karakter"""
        try:
            char1_anime = {anime['anime']['mal_id']: anime['anime']['name'] for anime in char1.get('anime', [])}
            char2_anime = {anime['anime']['mal_id']: anime['anime']['name'] for anime in char2.get('anime', [])}
            
            shared_anime = []
            for anime_id, anime_name in char1_anime.items():
                if anime_id in char2_anime:
                    shared_anime.append(anime_name)
            
            if shared_anime:
                return ", ".join(shared_anime[:3])  # Max 3 anime
        except:
            pass
        return None

    async def _send_basic_anime_info(self, ctx, anime):
        """Fallback untuk basic anime info"""
        embed = discord.Embed(
            title=f"🎌 {anime['title']}",
            url=anime['url'],
            color=0x2e51a2
        )
        
        embed.add_field(name="⭐ Score", value=anime.get('score', 'N/A'), inline=True)
        embed.add_field(name="📺 Episodes", value=anime.get('episodes', 'TBA'), inline=True)
        embed.add_field(name="📅 Status", value=anime.get('status', 'Unknown'), inline=True)
        
        if anime.get('images') and anime['images'].get('jpg'):
            thumbnail = anime['images']['jpg']['image_url']
            embed.set_thumbnail(url=thumbnail)
        
        await ctx.send(embed=embed)

    async def _send_basic_character_info(self, ctx, character):
        """Fallback untuk basic character info"""
        embed = discord.Embed(
            title=f"👤 {character['name']}",
            url=character['url'],
            color=0x3498db
        )
        
        if character.get('images') and character['images'].get('jpg'):
            thumbnail = character['images']['jpg']['image_url']
            embed.set_thumbnail(url=thumbnail)
        
        await ctx.send(embed=embed)

    async def _send_basic_voice_actor_info(self, ctx, voice_actor):
        """Fallback untuk basic voice actor info"""
        embed = discord.Embed(
            title=f"🎙️ {voice_actor['name']}",
            url=voice_actor['url'],
            color=0xe74c3c
        )
        
        if voice_actor.get('images') and voice_actor['images'].get('jpg'):
            thumbnail = voice_actor['images']['jpg']['image_url']
            embed.set_thumbnail(url=thumbnail)
        
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            if "429" in str(error):
                await ctx.send("⚠️ Rate limit exceeded! Tunggu beberapa detik sebelum request lagi.")
        elif isinstance(error, commands.CommandNotFound):
            pass


class AutoRecoverySystem:
    def __init__(self, bot):
        self.bot = bot
        self.error_counts = defaultdict(int)
        self.last_error_time = defaultdict(float)
        self.recovery_attempts = defaultdict(int)
        self.max_recovery_attempts = 3
        self.error_reset_time = 300  # 5 menit
        
        # Monitoring stats
        self.stats = {
            'total_errors': 0,
            'recoveries_performed': 0,
            'voice_reconnects': 0,
            'memory_cleanups': 0
        }
        
        print("✅ Auto Recovery System initialized!")
    
    def log_error(self, error_type, guild_id=None):
        """Log error dan cek apakah perlu recovery"""
        key = f"{error_type}:{guild_id}" if guild_id else error_type
        current_time = time.time()
        
        # Reset jika sudah lama
        if current_time - self.last_error_time.get(key, 0) > self.error_reset_time:
            self.error_counts[key] = 0
            self.recovery_attempts[key] = 0
        
        self.error_counts[key] += 1
        self.last_error_time[key] = current_time
        self.stats['total_errors'] += 1
        
        # Cek jika perlu recovery
        if self.error_counts[key] >= 3 and self.recovery_attempts[key] < self.max_recovery_attempts:
            print(f"⚠️ Multiple {error_type} errors detected, attempting auto-recovery...")
            return True
        
        return False
    
    async def perform_recovery(self, error_type, ctx=None, guild_id=None):
        """Perform auto-recovery berdasarkan error type"""
        try:
            if error_type == "voice_websocket":
                await self.recover_voice_connection(guild_id)
            elif error_type == "playback":
                await self.recover_playback(guild_id)
            elif error_type == "memory":
                await self.cleanup_memory()
            elif error_type == "ytdl":
                await self.reset_ytdl_cache()
            elif error_type == "general":
                await self.general_recovery(ctx)
            
            self.recovery_attempts[f"{error_type}:{guild_id}"] += 1
            self.stats['recoveries_performed'] += 1
            
            return True
        except Exception as e:
            print(f"❌ Recovery failed for {error_type}: {e}")
            return False
    
    async def recover_voice_connection(self, guild_id):
        """Recover voice connection yang error"""
        try:
            voice_client = None
            for vc in self.bot.voice_clients:
                if vc.guild.id == guild_id:
                    voice_client = vc
                    break
            
            if voice_client:
                # Disconnect dulu
                if voice_client.is_connected():
                    await voice_client.disconnect()
                    await asyncio.sleep(1)
                
                # Cari guild dan reconnect
                guild = self.bot.get_guild(guild_id)
                if guild and guild.voice_channels:
                    # Cari channel yang ada member
                    target_channel = None
                    for channel in guild.voice_channels:
                        if channel.members:
                            target_channel = channel
                            break
                    
                    if target_channel:
                        await target_channel.connect()
                        self.stats['voice_reconnects'] += 1
                        print(f"✅ Voice reconnected to {target_channel.name}")
                        return True
            
            return False
        except Exception as e:
            print(f"❌ Voice recovery error: {e}")
            return False
    
    async def recover_playback(self, guild_id):
        """Recover playback yang stuck"""
        try:
            # Clear guild player
            player.clear_guild(guild_id)
            
            # Stop voice client jika ada
            for vc in self.bot.voice_clients:
                if vc.guild.id == guild_id:
                    if vc.is_playing() or vc.is_paused():
                        vc.stop()
                    break
            
            await asyncio.sleep(1)
            print(f"✅ Playback recovered for guild {guild_id}")
            return True
        except Exception as e:
            print(f"❌ Playback recovery error: {e}")
            return False
    
    async def cleanup_memory(self):
        """Cleanup memory dan GC"""
        try:
            # Force garbage collection
            gc.collect()
            
            # Clear cache jika ada
            cache_dirs = ['yt-dlp_cache', '.cache', '__pycache__']
            for cache_dir in cache_dirs:
                if os.path.exists(cache_dir):
                    try:
                        import shutil
                        shutil.rmtree(cache_dir)
                    except:
                        pass
            
            # Clear player cache jika terlalu banyak
            if len(player.players) > 50:  # Jika lebih dari 50 guild
                oldest_guilds = sorted(player.players.keys())[:-30]  # Keep 30 terbaru
                for guild_id in oldest_guilds:
                    del player.players[guild_id]
            
            self.stats['memory_cleanups'] += 1
            print(f"✅ Memory cleaned up. Active guilds: {len(player.players)}")
            return True
        except Exception as e:
            print(f"❌ Memory cleanup error: {e}")
            return False
    
    async def reset_ytdl_cache(self):
        """Reset yt-dlp cache"""
        try:
            global ytdl_format_options
            # Force fresh options
            ytdl_format_options.update({
                'no_cache_dir': True,
                'cachedir': False,
                'force_generic_extractor': True,
            })
            print("✅ YTDL cache reset")
            return True
        except Exception as e:
            print(f"❌ YTDL reset error: {e}")
            return False
    
    async def general_recovery(self, ctx):
        """General recovery untuk command errors"""
        try:
            if ctx and hasattr(ctx, 'voice_client') and ctx.voice_client:
                if ctx.voice_client.is_playing():
                    ctx.voice_client.stop()
                await asyncio.sleep(0.5)
            
            # Clear typing jika stuck
            if ctx and hasattr(ctx, 'typing'):
                ctx.typing = False
            
            print("✅ General recovery performed")
            return True
        except Exception as e:
            print(f"❌ General recovery error: {e}")
            return False
    
    def get_stats(self):
        """Get recovery system stats"""
        return self.stats

# Initialize recovery system
recovery = None

# ============================
# ERROR HANDLER GLOBAL
# ============================

@bot.event
async def on_error(event, *args, **kwargs):
    """Global error handler"""
    try:
        print(f"⚠️ Global error in {event}:")
        traceback.print_exc()
        
        global recovery
        if recovery:
            recovery.log_error("general")
    except:
        pass

@bot.event
async def on_command_error(ctx, error):
    """Command error handler dengan auto-recovery"""
    try:
        error_type = "general"
        guild_id = ctx.guild.id if ctx.guild else None
        
        # Identify error type
        if isinstance(error, commands.CommandInvokeError):
            original = error.original
            
            if "voice" in str(original).lower() or "websocket" in str(original).lower():
                error_type = "voice_websocket"
            elif "play" in str(original).lower() or "audio" in str(original).lower():
                error_type = "playback"
            elif "memory" in str(original).lower() or "cache" in str(original).lower():
                error_type = "memory"
            elif "yt" in str(original).lower() or "download" in str(original).lower():
                error_type = "ytdl"
            
            # Log error
            if recovery and recovery.log_error(error_type, guild_id):
                # Coba recovery otomatis
                success = await recovery.perform_recovery(error_type, ctx, guild_id)
                
                if success:
                    await ctx.send(f"🔄 **Auto-recovery performed!** Error type: `{error_type}`")
                    return  # Jangan tampilkan error lagi
            
            # Jika masih error setelah recovery, tampilkan pesan user-friendly
            if "429" in str(original):
                await ctx.send("⚠️ **Rate limit!** Tunggu beberapa detik sebelum mencoba lagi.")
            elif "voice" in str(original).lower():
                await ctx.send("🔊 **Voice connection issue.** Coba `n.musicrestart` atau tunggu sebentar.")
            elif "search" in str(original).lower():
                await ctx.send("🔍 **Search failed.** Coba kata kunci yang berbeda atau URL langsung.")
            else:
                await ctx.send(f"❌ **Command error:** `{str(original)[:100]}`")
        
        elif isinstance(error, commands.CommandNotFound):
            pass  # Ignore command not found
        
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("🚫 **Missing permissions!**")
        
        elif isinstance(error, commands.BadArgument):
            await ctx.send("⚠️ **Invalid arguments!** Cek `n.help` untuk contoh penggunaan.")
        
        else:
            await ctx.send(f"❌ **Unexpected error:** `{str(error)[:100]}`")
        
        print(f"Command error in {ctx.command}: {error}")
        
    except Exception as e:
        print(f"Error in error handler (ironic): {e}")

# ============================
# SYSTEM MONITORING COMMANDS
# ============================

@bot.command(name='system', aliases=['sys', 'status', 'health'])
async def system_status(ctx):
    """Cek status sistem bot"""
    try:
        embed = discord.Embed(
            title="🖥️ System Status",
            color=0x00ff00
        )
        
        # Bot info
        embed.add_field(name="🤖 Bot Uptime", value=f"{round(time.time() - start_time)}s", inline=True)
        embed.add_field(name="📊 Servers", value=str(len(bot.guilds)), inline=True)
        embed.add_field(name="📈 Commands", value=str(len(bot.commands)), inline=True)
        
        # Voice info
        voice_clients = len(bot.voice_clients)
        playing = sum(1 for vc in bot.voice_clients if vc.is_playing())
        embed.add_field(name="🔊 Voice Clients", value=str(voice_clients), inline=True)
        embed.add_field(name="🎶 Playing", value=str(playing), inline=True)
        
        # Memory info
        import psutil
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        cpu_percent = process.cpu_percent()
        embed.add_field(name="💾 Memory", value=f"{memory_mb:.1f} MB", inline=True)
        embed.add_field(name="⚡ CPU", value=f"{cpu_percent:.1f}%", inline=True)
        
        # Player stats
        embed.add_field(name="🎵 Active Players", value=str(len(player.players)), inline=True)
        
        # Recovery stats
        if recovery:
            stats = recovery.get_stats()
            embed.add_field(name="🔄 Recoveries", value=str(stats['recoveries_performed']), inline=True)
            embed.add_field(name="⚠️ Total Errors", value=str(stats['total_errors']), inline=True)
        
        # System health indicator
        health = "✅ Excellent"
        if memory_mb > 200:
            health = "⚠️ High Memory"
        if cpu_percent > 70:
            health = "⚠️ High CPU"
        if stats['total_errors'] > 10:
            health = "⚠️ Many Errors"
        
        embed.add_field(name="🏥 System Health", value=health, inline=False)
        
        # Tips
        if health.startswith("⚠️"):
            embed.add_field(name="💡 Tips", value="Coba `n.cleanup` untuk cleanup memory", inline=False)
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Error getting system status: {e}")

@bot.command(name='cleanup', aliases=['gc', 'memclean'])
async def system_cleanup(ctx):
    """Cleanup system memory dan cache"""
    msg = await ctx.send("🧹 Cleaning up system...")
    
    try:
        # Force garbage collection
        collected = gc.collect()
        
        # Clear player cache untuk guild yang tidak aktif
        inactive_guilds = []
        for guild_id in list(player.players.keys()):
            voice_active = any(vc.guild.id == guild_id for vc in bot.voice_clients)
            if not voice_active:
                inactive_guilds.append(guild_id)
        
        for guild_id in inactive_guilds[:20]:  # Max 20 sekaligus
            del player.players[guild_id]
        
        # Clear downloads folder
        if os.path.exists(DOWNLOADS_PATH):
            for file in os.listdir(DOWNLOADS_PATH):
                if file.endswith(('.mp3', '.mp4', '.gif', '.temp')):
                    try:
                        os.remove(os.path.join(DOWNLOADS_PATH, file))
                    except:
                        pass
        
        # Clear yt-dlp cache
        cache_dirs = ['yt-dlp_cache', '.cache']
        for cache_dir in cache_dirs:
            if os.path.exists(cache_dir):
                try:
                    import shutil
                    shutil.rmtree(cache_dir)
                except:
                    pass
        
        await msg.edit(content=f"✅ **Cleanup complete!**\n"
                              f"• GC collected: {collected} objects\n"
                              f"• Inactive players removed: {len(inactive_guilds[:20])}\n"
                              f"• Cache cleared")
        
    except Exception as e:
        await msg.edit(content=f"❌ Cleanup error: {e}")

@bot.command(name='recovery', aliases=['rec', 'autofix'])
@commands.is_owner()
async def recovery_system(ctx):
    """Control recovery system (owner only)"""
    global recovery
    
    if not recovery:
        recovery = AutoRecoverySystem(bot)
        await ctx.send("✅ **Recovery system activated!**")
    else:
        stats = recovery.get_stats()
        embed = discord.Embed(
            title="🔧 Recovery System",
            color=0x00ff00
        )
        embed.add_field(name="🔄 Recoveries", value=stats['recoveries_performed'], inline=True)
        embed.add_field(name="⚠️ Total Errors", value=stats['total_errors'], inline=True)
        embed.add_field(name="🔊 Voice Reconnects", value=stats['voice_reconnects'], inline=True)
        embed.add_field(name="💾 Memory Cleanups", value=stats['memory_cleanups'], inline=True)
        
        # Error counts by type
        error_summary = "\n".join([f"{k}: {v}" for k, v in recovery.error_counts.items()][:10])
        if error_summary:
            embed.add_field(name="📊 Recent Errors", value=f"```{error_summary}```", inline=False)
        
        await ctx.send(embed=embed)

# ============================
# PERFORMANCE OPTIMIZATIONS
# ============================

# Optimize ytdl options untuk sistem rendah
ytdl_format_options.update({
    'socket_timeout': 10,
    'noprogress': True,
    'no_color': True,
    'simulate': True,
    'skip_download': True,
    'geo_bypass': True,
    'geo_bypass_country': 'US',
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
})

# Cache untuk search queries (reduces API calls)
search_cache = {}
CACHE_TIMEOUT = 300  # 5 menit

async def cached_search(query, search_func):
    """Cached search untuk mengurangi API calls"""
    cache_key = query.lower()
    
    if cache_key in search_cache:
        timestamp, data = search_cache[cache_key]
        if time.time() - timestamp < CACHE_TIMEOUT:
            return data
    
    # Jika tidak ada di cache atau expired
    data = await search_func(query)
    search_cache[cache_key] = (time.time(), data)
    
    # Cleanup cache lama
    current_time = time.time()
    expired_keys = [k for k, (t, _) in search_cache.items() if current_time - t > CACHE_TIMEOUT]
    for key in expired_keys:
        del search_cache[key]
    
    return data

# ============================
# STARTUP OPTIMIZATIONS
# ============================

start_time = time.time()

@bot.event
async def on_ready():
    """Optimized startup"""
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    
    # Initialize recovery system
    global recovery
    recovery = AutoRecoverySystem(bot)
    
    # Load cogs dengan error handling
    try:
        await bot.add_cog(MALCommands(bot))
        print("✅ MALCommands cog loaded!")
    except Exception as e:
        print(f"⚠️ MALCommands cog failed: {e}")
        if recovery:
            await recovery.perform_recovery("general")
    
    # Set status ringan
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, 
        name=f"{PREFIX}help"
    ))
    
    # Startup cleanup
    if os.path.exists(DOWNLOADS_PATH):
        for file in os.listdir(DOWNLOADS_PATH):
            if file.endswith('.temp'):
                try:
                    os.remove(os.path.join(DOWNLOADS_PATH, file))
                except:
                    pass
    
    print(f"🚀 Bot ready in {round(time.time() - start_time, 2)}s")

# ============================
# PERIODIC MAINTENANCE
# ============================

async def periodic_maintenance():
    """Background maintenance tasks - FIXED VERSION"""
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        try:
            # Cleanup cache setiap 30 menit
            if 'search_cache' in globals():
                search_cache.clear()
            
            # Cleanup downloads folder setiap jam
            if os.path.exists(DOWNLOADS_PATH):
                current_time = time.time()
                for file in os.listdir(DOWNLOADS_PATH):
                    filepath = os.path.join(DOWNLOADS_PATH, file)
                    try:
                        # Hapus file temp atau file lama (>1 jam)
                        if file.endswith('.temp') or (current_time - os.path.getmtime(filepath) > 3600):
                            os.remove(filepath)
                    except:
                        pass
            
            # Auto-disconnect dari voice yang idle > 30 menit
            for vc in bot.voice_clients:
                if vc.is_connected() and not vc.is_playing() and not vc.is_paused():
                    guild_id = vc.guild.id
                    guild_player = get_guild_player_by_id(guild_id)
                    # Jika queue kosong dan sendirian di channel
                    if not guild_player['queue'] and len(vc.channel.members) == 1:
                        await vc.disconnect()
                        player.clear_guild(guild_id)
            
            # Memory cleanup jika tinggi
            try:
                import psutil
                process = psutil.Process()
                if process.memory_info().rss > 200 * 1024 * 1024:  # >200MB
                    gc.collect()
            except:
                pass
            
            # Tambahkan delay 5 menit (300 detik) untuk mencegah beban berlebihan
            await asyncio.sleep(300)
            
        except Exception as e:
            print(f"[Maintenance Error] {e}")
            await asyncio.sleep(60)  # Delay lebih pendek jika error

# ============================
# BOT STARTUP HOOK (FIXED)
# ============================

@bot.event
async def on_ready():
    """Optimized startup dengan auto-maintenance"""
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    print(f'📊 Bot is in {len(bot.guilds)} guilds')
    
    # Initialize recovery system
    global recovery
    recovery = AutoRecoverySystem(bot)
    
    # Load MALCommands cog dengan error handling
    try:
        # Cek dulu apakah cog sudah dimuat
        if 'MALCommands' not in [cog.__class__.__name__ for cog in bot.cogs.values()]:
            await bot.add_cog(MALCommands(bot))
            print("✅ MALCommands cog loaded!")
    except Exception as e:
        print(f"⚠️ MALCommands cog failed: {e}")
        if recovery:
            await recovery.perform_recovery("general", None, None)
    
    # Set status yang lebih ringan
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, 
        name=f"{PREFIX}help"
    ))
    
    # Startup cleanup
    if os.path.exists(DOWNLOADS_PATH):
        for file in os.listdir(DOWNLOADS_PATH):
            if file.endswith('.temp'):
                try:
                    os.remove(os.path.join(DOWNLOADS_PATH, file))
                except:
                    pass
    
    # Jalankan periodic_maintenance sebagai background task
    bot.loop.create_task(periodic_maintenance())
    print("✅ Background maintenance task started!")
    
    print(f"🚀 Bot ready and operational!")

# ============================
# FIXED GLOBAL PLAYER STORAGE
# ============================

# Pastikan guild_players didefinisikan secara global
guild_players = player.players  # Alias untuk kompatibilitas

async def main():
    """Main async function untuk menjalankan bot"""
    try:
        async with bot:
            await bot.start(BOT_TOKEN)
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot error: {e}")
        import traceback
        traceback.print_exc()

# ============================
# BOT START
# ============================

if __name__ == "__main__":
    bot.run(BOT_TOKEN)