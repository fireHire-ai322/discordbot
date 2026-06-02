

# 👇 حط التوكن الجديد بتاعك هنا بالظبط بين علامتين التنص
# Configuration
DISCORD_TOKEN = 'MTUxMDkzODY3NzUyNDY5MzA5Mg.Gb-taS.2NenFkxIWu9ERzWovdKDyibV_rpWCX0OnqnnMo'
CHANNEL_ID = 1461466432679182684  # حط الـ ID بتاع روم the-fire هنا
STAFF_ROLE_ID = 1461551955909410972   # 👇 حط هنا ID رتبة الموظفين (Staff/Callers/etc)
LOG_CHANNEL_ID = 1511158605875904622  # ID روم اللوجات أو الإدارة

import discord
from discord.ext import commands, tasks
from datetime import datetime
import asyncio
import json
import os
from flask import Flask
from threading import Thread

# تفعيل خادم ويب صغير لضمان بقاء البوت حياً على منصات الاستضافة المجانية
app = Flask('')
@app.route('/')
def home():
    return "Attendance Bot is running 24/7!"

def run_web_server():
    app.run(host='0.0.0.0', port=8080)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
bot = commands.Bot(command_prefix="!", intents=intents)

logged_in_users = {}
TRACKER_FILE = "weekly_attendance.json"

# دالات لإدارة ملف التتبع الأسبوعي للموظفين
def load_weekly_tracker():
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    return []

def save_to_weekly_tracker(user_id_str):
    tracker = load_weekly_tracker()
    if user_id_str not in tracker:
        tracker.append(user_id_str)
        with open(TRACKER_FILE, "w") as f:
            json.dump(tracker, f)

def clear_weekly_tracker():
    if os.path.exists(TRACKER_FILE):
        os.remove(TRACKER_FILE)

def create_attendance_embed():
    embed = discord.Embed(
        title="📝 Daily Attendance System",
        description="Please click the buttons below to manage your shift status for today:",
        color=discord.Color.blue()
    )
    embed.add_field(name="🟢 Log In", value="Click when you start your shift.", inline=True)
    embed.add_field(name="🔴 Log Out", value="Click when you finish your shift.", inline=True)
    
    if logged_in_users:
        users_list = "\n".join([f"🟢 {user_mention} *(at {time})*" for user_mention, time in logged_in_users.items()])
    else:
        users_list = "*No one checked in yet.*"
        
    embed.add_field(name="📊 Active Staff Today", value=users_list, inline=False)
    return embed

class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Log In 🟢", style=discord.ButtonStyle.green, custom_id="login_button")
    async def login_button_callback(self, interaction: discord.Interaction, button: discord.ui.button):
        user = interaction.user
        current_time = datetime.now().strftime("%I:%M %p")
        
        if user.mention not in logged_in_users:
            logged_in_users[user.mention] = current_time
            save_to_weekly_tracker(str(user.id)) # حفظه في قائمة الملتزمين أسبوعياً
            await interaction.message.edit(embed=create_attendance_embed())
            await interaction.response.send_message(f"Logged in successfully at {current_time} 🚀", ephemeral=True)
        else:
            await interaction.response.send_message("You are already logged in!", ephemeral=True)

    @discord.ui.button(label="Log Out 🔴", style=discord.ButtonStyle.red, custom_id="logout_button")
    async def logout_button_callback(self, interaction: discord.Interaction, button: discord.ui.button):
        user = interaction.user
        current_time = datetime.now().strftime("%I:%M %p")
        
        if user.mention in logged_in_users:
            del logged_in_users[user.mention]
            await interaction.message.edit(embed=create_attendance_embed())
            await interaction.response.send_message(f"Logged out successfully at {current_time} 🛑", ephemeral=True)
        else:
            await interaction.response.send_message("You haven't logged in yet today!", ephemeral=True)

@tasks.loop(minutes=1)
async def auto_attendance_scheduler():
    now = datetime.now()
    
    # تفريغ قائمة التتبع الأسبوعي مع بداية أول شيفت في الأسبوع (الإثنين الساعة 4)
    if now.weekday() == 0 and now.hour == 16 and now.minute == 0:
        clear_weekly_tracker()

    # 1. الساعة 4:00 مساءً (من الإثنين للجمعة) -> فتح الحضور اليومي
    if now.weekday() in [0, 1, 2, 3, 4] and now.hour == 16 and now.minute == 0:
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            logged_in_users.clear()
            await channel.purge(limit=5, check=lambda m: m.author == bot.user)
            await channel.send(embed=create_attendance_embed(), view=AttendanceView())
            await asyncio.sleep(60)

    # 2. الساعة 4:30 مساءً (من الإثنين للجمعة) -> تذكير للمتأخرين (أونلاين وأوفلاين) + تقرير للإدارة
    if now.weekday() in [0, 1, 2, 3, 4] and now.hour == 16 and now.minute == 30:
        channel = bot.get_channel(CHANNEL_ID)
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        
        if channel and log_channel:
            guild = channel.guild
            role = guild.get_role(STAFF_ROLE_ID)
            
            if role:
                reminded_users = []
                closed_dms_users = []
                
                for member in role.members:
                    if member.bot:
                        continue
                    # التذكير لكل من لم يسجل حضور بغض النظر عن حالته أونلاين أو أوفلاين
                    if member.mention not in logged_in_users:
                        try:
                            dm_embed = discord.Embed(
                                title="⚡ Shift Reminder! ⚡",
                                description=f"Hey {member.name},\n\nThe shift started 30 minutes ago! 😉 Don't forget to go to {channel.mention} and click the **Log In 🟢** button to register your attendance. Let's crush it today! 💪🔥",
                                color=discord.Color.orange()
                            )
                            await member.send(embed=dm_embed)
                            reminded_users.append(member.mention)
                        except discord.Forbidden:
                            closed_dms_users.append(member.mention)
                
                # إرسال التقرير اليومي الخاص بك في روم اللوجات
                report_embed = discord.Embed(title="📊 Daily 4:30 PM DM Reminder Report", color=discord.Color.gold())
                if reminded_users:
                    report_embed.add_field(name="📩 Successfully Reminded via DM:", value=", ".join(reminded_users), inline=False)
                if closed_dms_users:
                    report_embed.add_field(name="⚠️ Failed to DM (DMs Closed):", value=", ".join(closed_dms_users), inline=False)
                if not reminded_users and not closed_dms_users:
                    report_embed.description = "✅ Perfect! Everyone has already logged in before 4:30 PM today."
                
                await log_channel.send(embed=report_embed)
            await asyncio.sleep(60)

    # 3. الساعة 11:00 مساءً يوم الجمعة -> فحص وطرد المتقاعسين طوال الأسبوع تلقائياً
    if now.weekday() == 4 and now.hour == 23 and now.minute == 0:
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        channel = bot.get_channel(CHANNEL_ID)
        
        if channel and log_channel:
            guild = channel.guild
            role = guild.get_role(STAFF_ROLE_ID)
            weekly_active_ids = load_weekly_tracker()
            kicked_users = []
            
            if role:
                for member in role.members:
                    if member.bot:
                        continue
                    # إذا لم يتواجد معرفه في قائمة المسجلين طوال الأسبوع
                    if str(member.id) not in weekly_active_ids:
                        try:
                            # إرسال رسالة وداع في الخاص قبل الطرد لإعلامه بالسبب
                            await member.send("You have been removed from the server due to complete inactivity and failure to log in all week. 🛑")
                            await member.kick(reason="Inactivity - Failed to log in once from Monday to Friday.")
                            kicked_users.append(f"**{member.name}**")
                        except Exception as e:
                            print(f"Could not kick {member.name}: {e}")
                
                # تقرير الطرد الأسبوعي للمشرفين
                kick_report = discord.Embed(title="🚨 Weekly Inactivity Purge Report", color=discord.Color.red())
                if kicked_users:
                    kick_report.description = f"The following members were kicked automatically for not logging in at all this week:\n\n" + "\n".join(kicked_users)
                else:
                    kick_report.description = "✅ Great news! No one was kicked this week. All staff members logged in at least once."
                await log_channel.send(embed=kick_report)
            await asyncio.sleep(60)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    bot.add_view(AttendanceView())
    if not auto_attendance_scheduler.is_running():
        auto_attendance_scheduler.start()

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_attendance(ctx):
    await ctx.message.delete()
    logged_in_users.clear()
    await ctx.send(embed=create_attendance_embed(), view=AttendanceView())

# تشغيل خادم الويب في خلفية منفصلة
Thread(target=run_web_server).start()
bot.run(DISCORD_TOKEN)
