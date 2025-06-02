import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
from collections import deque
from config import BOT_TOKEN, PREFIX

# Suppress noise
# youtube_dl.utils.bug_reports_message = lambda: ''

# Configuration
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

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

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

player = MusicPlayer()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)

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

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="n.help"))

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

@bot.command(aliases=['p'])
async def play(ctx, *, query):
    """Play a song or add to queue - supports playlists"""
    if not ctx.author.voice:
        return await ctx.send("🚫 You need to be in a voice channel!")
    
    async with ctx.typing():
        try:
            # Clean the query
            clean_query = query.strip()
            if not clean_query:
                return await ctx.send("🚫 Please provide a song name or URL")

            # Check if it's a playlist URL
            if 'list=' in clean_query.lower() and ('youtube.com' in clean_query.lower() or 'youtu.be' in clean_query.lower()):
                try:
                    # Create playlist extractor
                    ytdl_playlist = youtube_dl.YoutubeDL({
                        **ytdl_format_options,
                        'extract_flat': True,
                        'noplaylist': False
                    })
                    
                    # Get playlist info
                    playlist_data = await bot.loop.run_in_executor(
                        None, 
                        lambda: ytdl_playlist.extract_info(clean_query, download=False)
                    )
                    
                    if not playlist_data or 'entries' not in playlist_data:
                        return await ctx.send("❌ Couldn't process that playlist or playlist is empty")
                    
                    songs = []
                    for entry in playlist_data['entries']:
                        if entry:
                            songs.append(Song(entry, ctx.author))
                            if len(songs) >= 100:  # Limit to 100 songs
                                break
                    
                    if not songs:
                        return await ctx.send("❌ No valid songs found in playlist")
                    
                    # Connect to voice channel
                    if not ctx.voice_client:
                        await ctx.author.voice.channel.connect()
                    
                    # Add songs to queue
                    player.playlist_mode = True
                    for song in songs:
                        player.queue.append(song)
                    player.playlist_mode = False
                    
                    # Start playing if not already playing
                    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                        await play_next(ctx)
                    
                    return await ctx.send(f"🎵 Added {len(songs)} songs from playlist: {playlist_data['title']}")
                
                except Exception as e:
                    return await ctx.send(f"❌ Playlist error: {str(e)}")

            # Handle single song search
            try:
                # Create new YTDL instance for this search
                with youtube_dl.YoutubeDL(ytdl_format_options) as ytdl_instance:
                    # Determine if it's a URL or search query
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
                        return await ctx.send("❌ No results found")
                    
                    if 'entries' in data:
                        if not data['entries']:
                            return await ctx.send("❌ No results found")
                        data = data['entries'][0]
                    
                    song = Song(data, ctx.author)
                    
                    # Connect to voice channel
                    if not ctx.voice_client:
                        await ctx.author.voice.channel.connect()
                    
                    # Add to queue or play immediately
                    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                        player.queue.append(song)
                        embed = discord.Embed(
                            description=f"🎵 Added to queue: [{song.title}]({song.url})",
                            color=0x00ff00
                        )
                        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                        await ctx.send(embed=embed)
                    else:
                        await play_song(ctx.voice_client, song)
                        embed = discord.Embed(
                            description=f"🎶 Now playing: [{song.title}]({song.url})",
                            color=0x00ff00
                        )
                        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                        if song.thumbnail:
                            embed.set_thumbnail(url=song.thumbnail)
                        await ctx.send(embed=embed)
            
            except Exception as e:
                await ctx.send(f"❌ Error processing song: {str(e)}")

        except Exception as e:
            await ctx.send(f"❌ Unexpected error: {str(e)}")

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
async def help(ctx):
    """Show all commands"""
    prefix = ctx.prefix  # This will be 'n.' in our case
    embed = discord.Embed(title=f"{prefix}🎵 Music Bot Commands", color=0x00ff00)
    
    commands = [
        (f"{prefix}play [query/URL]", "Play a song or add to queue"),
        (f"{prefix}queue [page]", "Show current queue (10 songs per page)"),
        (f"{prefix}skip", "Skip current song"),
        (f"{prefix}loop", "Toggle current song loop"),
        (f"{prefix}loopqueue", "Toggle queue looping"),
        (f"{prefix}remove [position]", "Remove song from queue"),
        (f"{prefix}clear", "Clear the queue"),
        (f"{prefix}volume [0-100]", "Set playback volume"),
        (f"{prefix}shuffle", "Shuffle the queue"),
        (f"{prefix}move [from] [to]", "Move song in queue"),
        (f"{prefix}stop", "Stop playback and disconnect")
    ]
    
    for name, value in commands:
        embed.add_field(name=name, value=value, inline=False)
    
    embed.set_footer(text=f"Use {prefix} before each command")
    await ctx.send(embed=embed)

@bot.command()
async def stop(ctx):
    """Stop playback and disconnect"""
    if not ctx.voice_client:
        return await ctx.send("ℹ️ I'm not in a voice channel!")
    
    player.clear()
    player.current_song = None
    await ctx.voice_client.disconnect()
    await ctx.message.add_reaction("🛑")

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

bot.run(BOT_TOKEN)