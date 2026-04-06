import discord
from discord.ext import tasks
import os
import sqlite3
import datetime
import asyncio
import re
import json
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

# ================= DATABASE =================
conn = sqlite3.connect("modbot.db")
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS roles
(guild_id INTEGER PRIMARY KEY, mod_roles TEXT, admin_roles TEXT)""")

c.execute("""CREATE TABLE IF NOT EXISTS warnings
(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, user_id INTEGER, reason TEXT)""")

c.execute("""CREATE TABLE IF NOT EXISTS temp_roles
(id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, role_id INTEGER, expires_at TEXT)""")

c.execute("""CREATE TABLE IF NOT EXISTS automod
(guild_id INTEGER PRIMARY KEY, spam INTEGER, links INTEGER, caps INTEGER)""")

conn.commit()

# ================= HELPERS =================
def get_roles(guild_id):
    c.execute("SELECT mod_roles, admin_roles FROM roles WHERE guild_id=?", (guild_id,))
    r = c.fetchone()
    if not r:
        return [], []
    return json.loads(r[0] or "[]"), json.loads(r[1] or "[]")

def has_mod(member):
    mods, _ = get_roles(member.guild.id)
    return any(r.id in mods for r in member.roles)

def has_admin(member):
    _, admins = get_roles(member.guild.id)
    return any(r.id in admins for r in member.roles) or member == member.guild.owner

def parse_duration(s):
    matches = re.findall(r"(\d+)([dhm])", s.lower())
    seconds = 0
    for v,u in matches:
        v = int(v)
        if u=="d": seconds+=v*86400
        if u=="h": seconds+=v*3600
        if u=="m": seconds+=v*60
    return datetime.timedelta(seconds=seconds)

# ================= EVENTS =================
@bot.event
async def on_ready():
    print(f"🔥 Logged in as {bot.user}")
    check_temp_roles.start()

@bot.event
async def on_member_join(member):
    try:
        await member.send(f"👋 Welcome to {member.guild.name}!")
    except:
        pass

# ================= AUTOMOD =================
spam_tracker = {}

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    c.execute("SELECT spam, links, caps FROM automod WHERE guild_id=?", (message.guild.id,))
    s = c.fetchone()
    if not s:
        return

    spam_on, links_on, caps_on = s

    # Spam
    if spam_on:
        user = message.author.id
        now = datetime.datetime.now().timestamp()
        spam_tracker.setdefault(user, []).append(now)
        spam_tracker[user] = [t for t in spam_tracker[user] if now-t < 5]

        if len(spam_tracker[user]) > 5:
            await message.delete()
            return

    # Links
    if links_on and "http" in message.content:
        await message.delete()
        return

    # Caps
    if caps_on and message.content.isupper() and len(message.content) > 6:
        await message.delete()
        return

# ================= TEMP ROLE LOOP =================
@tasks.loop(seconds=30)
async def check_temp_roles():
    now = datetime.datetime.now().isoformat()
    c.execute("SELECT id,guild_id,user_id,role_id FROM temp_roles WHERE expires_at<=?", (now,))
    rows = c.fetchall()

    for row in rows:
        _, g,u,r = row
        guild = bot.get_guild(g)
        if guild:
            member = guild.get_member(u)
            role = guild.get_role(r)
            if member and role:
                await member.remove_roles(role)
        c.execute("DELETE FROM temp_roles WHERE id=?", (row[0],))
    conn.commit()

# ================= COMMANDS =================

# 🔧 SET ROLES
@bot.slash_command(description="Set mod/admin roles")
async def setrole(ctx, type: str, role: discord.Role):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.respond("❌ Admin only", ephemeral=True)

    mods, admins = get_roles(ctx.guild.id)

    if type == "mod":
        mods.append(role.id)
    elif type == "admin":
        admins.append(role.id)

    c.execute("INSERT OR REPLACE INTO roles VALUES (?,?,?)",
              (ctx.guild.id, json.dumps(mods), json.dumps(admins)))
    conn.commit()

    await ctx.respond("✅ Role set")

# ⚠️ WARN
@bot.slash_command(description="Warn a user")
async def warn(ctx, member: discord.Member, reason: str):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    c.execute("INSERT INTO warnings (guild_id,user_id,reason) VALUES (?,?,?)",
              (ctx.guild.id, member.id, reason))
    conn.commit()

    await ctx.respond(f"⚠️ Warned {member}")

    try:
        await member.send(f"You were warned: {reason}")
    except:
        pass

    c.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
              (ctx.guild.id, member.id))
    if c.fetchone()[0] >= 3:
        await member.timeout(datetime.timedelta(minutes=10))

# 🔨 BAN
@bot.slash_command(description="Ban a user")
async def ban(ctx, member: discord.Member, reason: str = "No reason"):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    try:
        await member.send(f"You were banned: {reason}")
    except:
        pass

    await member.ban(reason=reason)
    await ctx.respond(f"🔨 Banned {member}")

# 👢 KICK
@bot.slash_command(description="Kick a user")
async def kick(ctx, member: discord.Member, reason: str = "No reason"):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    try:
        await member.send(f"You were kicked: {reason}")
    except:
        pass

    await member.kick(reason=reason)
    await ctx.respond(f"👢 Kicked {member}")

# 🔇 MUTE
@bot.slash_command(description="Timeout a user")
async def mute(ctx, member: discord.Member, duration: str):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    delta = parse_duration(duration)
    await member.timeout(delta)
    await ctx.respond(f"🔇 Muted for {duration}")

@bot.slash_command(description="Unmute user")
async def unmute(ctx, member: discord.Member):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    await member.timeout(None)
    await ctx.respond("✅ Unmuted")

# 🧹 PURGE
@bot.slash_command(description="Delete messages")
async def purge(ctx, amount: int):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    await ctx.channel.purge(limit=amount)
    await ctx.respond(f"Deleted {amount}", ephemeral=True)

# 🔒 LOCK / UNLOCK
@bot.slash_command(description="Lock channel")
async def lock(ctx):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.respond("🔒 Locked")

@bot.slash_command(description="Unlock channel")
async def unlock(ctx):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.respond("🔓 Unlocked")

# 🐢 SLOWMODE
@bot.slash_command(description="Set slowmode")
async def slowmode(ctx, seconds: int):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.respond(f"Slowmode {seconds}s")

# 🎭 TEMP ROLE
@bot.slash_command(description="Give temporary role")
async def role_temp(ctx, member: discord.Member, role: discord.Role, duration: str):
    if not has_admin(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    delta = parse_duration(duration)
    expire = (datetime.datetime.now() + delta).isoformat()

    await member.add_roles(role)

    c.execute("INSERT INTO temp_roles (guild_id,user_id,role_id,expires_at) VALUES (?,?,?,?)",
              (ctx.guild.id, member.id, role.id, expire))
    conn.commit()

    await ctx.respond(f"✅ Temp role for {duration}")

# 🤖 AUTOMOD
@bot.slash_command(description="Toggle automod")
async def automod(ctx, setting: str, value: str):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.respond("Admin only", ephemeral=True)

    spam, links, caps = 0,0,0

    if value == "on":
        if setting=="spam": spam=1
        if setting=="links": links=1
        if setting=="caps": caps=1

    c.execute("INSERT OR REPLACE INTO automod VALUES (?,?,?,?)",
              (ctx.guild.id, spam, links, caps))
    conn.commit()

    await ctx.respond("✅ Automod updated")

# ================= RUN =================
bot.run(TOKEN)
