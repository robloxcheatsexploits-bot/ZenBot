import discord
from discord.ext import tasks
import os
import sqlite3
import datetime
import re
import json
import asyncio
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = discord.Bot(intents=intents)

# ====================== DATABASE ======================
conn = sqlite3.connect('modbot.db')
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS guild_settings 
             (guild_id INTEGER PRIMARY KEY, mod_roles TEXT DEFAULT "[]", admin_roles TEXT DEFAULT "[]")''')

c.execute('''CREATE TABLE IF NOT EXISTS temp_roles 
             (id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, 
              role_id INTEGER, expires_at TEXT)''')
conn.commit()

# ====================== UTILS ======================
def get_mod_roles(guild_id):
    c.execute("SELECT mod_roles FROM guild_settings WHERE guild_id=?", (guild_id,))
    r = c.fetchone()
    return json.loads(r[0]) if r else []

def get_admin_roles(guild_id):
    c.execute("SELECT admin_roles FROM guild_settings WHERE guild_id=?", (guild_id,))
    r = c.fetchone()
    return json.loads(r[0]) if r else []

def save_mod_roles(guild_id, roles):
    c.execute("INSERT OR REPLACE INTO guild_settings VALUES (?, ?, COALESCE((SELECT admin_roles FROM guild_settings WHERE guild_id=?),'[]'))",
              (guild_id, json.dumps(roles), guild_id))
    conn.commit()

def save_admin_roles(guild_id, roles):
    c.execute("INSERT OR REPLACE INTO guild_settings VALUES (?, COALESCE((SELECT mod_roles FROM guild_settings WHERE guild_id=?),'[]'), ?)",
              (guild_id, guild_id, json.dumps(roles)))
    conn.commit()

def parse_duration(dur):
    matches = re.findall(r'(\d+)([dhm])', dur.lower())
    if not matches:
        raise ValueError("Use format like 30m, 2h, 1d")
    seconds = 0
    for v, u in matches:
        v = int(v)
        if u == "d": seconds += v * 86400
        if u == "h": seconds += v * 3600
        if u == "m": seconds += v * 60
    return datetime.timedelta(seconds=seconds)

def has_mod(member):
    return any(r.id in get_mod_roles(member.guild.id) for r in member.roles)

def has_admin(member):
    return any(r.id in get_admin_roles(member.guild.id) for r in member.roles) or member == member.guild.owner

# ====================== TEMP ROLE LOOP ======================
async def remove_temp_role(guild_id, user_id, role_id):
    guild = bot.get_guild(guild_id)
    if not guild: return
    member = guild.get_member(user_id)
    role = guild.get_role(role_id)
    if member and role:
        await member.remove_roles(role)

@tasks.loop(seconds=30)
async def check_temp_roles():
    now = datetime.datetime.now().isoformat()
    c.execute("SELECT id, guild_id, user_id, role_id FROM temp_roles WHERE expires_at <= ?", (now,))
    rows = c.fetchall()
    for row in rows:
        _, g, u, r = row
        asyncio.create_task(remove_temp_role(g, u, r))
        c.execute("DELETE FROM temp_roles WHERE id=?", (row[0],))
    conn.commit()

@check_temp_roles.before_loop
async def before_loop():
    await bot.wait_until_ready()

# ====================== EVENTS ======================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    check_temp_roles.start()

@bot.event
async def on_member_join(member):
    channel = member.guild.get_channel(1486850964076888295)
    if channel:
        await channel.send(f"👋 Welcome {member.mention}!")

# ====================== SETUP ======================
@bot.slash_command(description="Setup roles")
async def setup(ctx, action: str, role: discord.Role = None):

    if not ctx.author.guild_permissions.administrator:
        return await ctx.respond("❌ Admin only", ephemeral=True)

    gid = ctx.guild.id

    if action == "list":
        mods = [ctx.guild.get_role(r) for r in get_mod_roles(gid)]
        admins = [ctx.guild.get_role(r) for r in get_admin_roles(gid)]
        return await ctx.respond(f"Mods: {mods}\nAdmins: {admins}")

    if not role:
        return await ctx.respond("❌ Provide role")

    if action == "mod_add":
        roles = get_mod_roles(gid)
        roles.append(role.id)
        save_mod_roles(gid, roles)

    elif action == "admin_add":
        roles = get_admin_roles(gid)
        roles.append(role.id)
        save_admin_roles(gid, roles)

    await ctx.respond("✅ Done")

# ====================== MOD COMMANDS ======================
@bot.slash_command()
async def ban(ctx, member: discord.Member, reason: str = "No reason"):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)
    await member.ban(reason=reason)
    await ctx.respond(f"✅ Banned {member}")

@bot.slash_command()
async def kick(ctx, member: discord.Member, reason: str = "No reason"):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)
    await member.kick(reason=reason)
    await ctx.respond(f"✅ Kicked {member}")

@bot.slash_command()
async def mute(ctx, member: discord.Member, duration: str):
    if not has_mod(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)
    delta = parse_duration(duration)
    await member.timeout(delta)
    await ctx.respond(f"✅ Muted {member} for {duration}")

# ====================== ROLE COMMANDS ======================
@bot.slash_command()
async def role_add(ctx, member: discord.Member, role: discord.Role):
    if not has_admin(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)
    await member.add_roles(role)
    await ctx.respond("✅ Role added")

@bot.slash_command()
async def role_temp(ctx, member: discord.Member, role: discord.Role, duration: str):
    if not has_admin(ctx.author):
        return await ctx.respond("❌ No permission", ephemeral=True)

    delta = parse_duration(duration)
    expire = (datetime.datetime.now() + delta).isoformat()

    await member.add_roles(role)

    c.execute("INSERT INTO temp_roles (guild_id, user_id, role_id, expires_at) VALUES (?, ?, ?, ?)",
              (ctx.guild.id, member.id, role.id, expire))
    conn.commit()

    await ctx.respond(f"✅ Temp role added for {duration}")

# ====================== RUN ======================
bot.run(TOKEN)