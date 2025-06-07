# Reika Bot 

Reika Bot is a personal music bot that run in your computer. Use it at your own risk!

## Installation

Download Python v 3.11

https://www.python.org/downloads/release/python-3110/ or https://apps.microsoft.com/detail/9NRWMJP3717K?hl=en-us&gl=ID&ocid=pdpshare

Clone the github repository.

```bash
git clone https://github.com/nakzuwu/Reika-Bot-Discord.git
```

Install the requirements.

```bash
pip install -r requirements.txt
```

Add config.py into your folder.

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
