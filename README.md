# Reika Bot

Reika Bot is bot inspired from whatsapp bot Hayasaka by Dendy. We have anime, music, download, and more feature available and easy to install. If you have any other question feel free to dm my discord @nkzw

## Installation

Download Python v 3.9 or newer (3.12 recommended)

https://www.python.org/downloads/release/python-3110/ or https://apps.microsoft.com/detail/9NRWMJP3717K?hl=en-us&gl=ID&ocid=pdpshare

Install FFMPEG (Required!)

https://www.ffmpeg.org

Clone the github repository.

```bash
git clone https://github.com/nakzuwu/Reika-Bot-Discord.git
```

Create venv (Optional)

```bash
python -m venv venv
```

Install the requirements.

```bash
pip install -r requirements.txt
```

Create config.py in your folder.

```bash
BOT_TOKEN = 'Your_bot_token' //copy your bot token from discord console https://discord.com/developers/applications
PREFIX = 'n.' //add your own previx like m! or n.
```

## Usage

You can run it simply run

```bash
python musicbot.py
```

OR

If you a windows user, u can just click startbot.bat. You can make the bot automatically run simply by add the startbot.bat file into your startup folder (C:\Users\yourUser\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup) and add the line

```bash
@echo off
cd /d "Directoty\to\your\Bot Music" //add your directory
python musicbot.py
pause
```
