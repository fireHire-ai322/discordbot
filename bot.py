import discord
from discord.ext import commands, tasks
from datetime import datetime
import pytz
import asyncio
import json
import os

# ============================================================
# ⚙️ الإعدادات
# ============================================================
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = 1461466432679182684
STAFF_ROLE_ID = 1461551955909410972
LOG_CHANNEL_ID = 1511158605875904622

CAIRO_TZ = pytz.timezone("Africa/Cairo")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

logged_in_users = {}        # {mention: login_time_str}
login_timestamps = {}       # {mention: datetime object} لحساب مدة الشيفت
TRACKER_FILE = "weekly_attendance.json"
LEADERBOARD_FILE = "leaderboard.json"


# ============================================================
# دالات ملف التتبع الأسبوعي
# ============================================================
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


# ============================================================
# دالات الليدربورد
# ============================================================
def load_leaderboard():
    if os.path.exists(LEADERBOARD_FILE):
        with open(LEADERBOARD_FILE, "r") as f:
            return json.load(f)
    return {}

def save_leaderboard(data):
    with open(LEADERBOARD_FILE, "w") as f:
        json.dump(data, f)

def add_attendance_point(user_id_str):
    """زيادة نقطة حضور لكل موظف سجل Login هذا الأسبوع"""
    lb = load_leaderboard()
    if user_id_str not in lb:
        lb[user_id_str] = 0
    lb[user_id_str] += 1
    save_leaderboard(lb)

def reset_weekly_leaderboard():
    """تصفير نقاط الأسبوع"""
    save_leaderboard({})


# ============================================================
# الـ Embed بتاع الحضور
# ============================================================
def create_attendance_embed():
    embed = discord.Embed(
        title="📝 Daily Attendance System",
        description="Please click the buttons below to manage your shift status for today:",
        color=discord.Color.blue(),
    )
    embed.add_field(name="🟢 Log In", value="Click when you start your shift.", inline=True)
    embed.add_field(name="🔴 Log Out", value="Click when you finish your shift.", inline=True)

    if logged_in_users:
        users_list = "\n".join(
            [f"🟢 {user_mention} *(at {time})*" for user_mention, time in logged_in_users.items()]
        )
    else:
        users_list = "*No one checked in yet.*"

    embed.add_field(name="📊 Active Staff Today", value=users_list, inline=False)
    return embed


# ============================================================
# الأزرار
# ============================================================
class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Log In 🟢", style=discord.ButtonStyle.green, custom_id="login_button")
    async def login_button_callback(self, interaction: discord.Interaction, button: discord.ui.button):
        user = interaction.user
        current_time = datetime.now(CAIRO_TZ).strftime("%I:%M %p")
        now_dt = datetime.now(CAIRO_TZ)

        if user.mention not in logged_in_users:
            logged_in_users[user.mention] = current_time
            login_timestamps[user.mention] = now_dt
            save_to_weekly_tracker(str(user.id))
            add_attendance_point(str(user.id))  # نقطة في الليدربورد

            await interaction.message.edit(embed=create_attendance_embed())
            await interaction.response.send_message(
                f"Logged in successfully at {current_time} 🚀", ephemeral=True
            )

            # إشعار اللوج روم
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="🟢 Staff Logged In",
                    description=f"{user.mention} started their shift at **{current_time}**",
                    color=discord.Color.green()
                )
                log_embed.set_thumbnail(url=user.display_avatar.url)
                await log_channel.send(embed=log_embed)
        else:
            await interaction.response.send_message("You are already logged in!", ephemeral=True)

    @discord.ui.button(label="Log Out 🔴", style=discord.ButtonStyle.red, custom_id="logout_button")
    async def logout_button_callback(self, interaction: discord.Interaction, button: discord.ui.button):
        user = interaction.user
        current_time = datetime.now(CAIRO_TZ).strftime("%I:%M %p")
        now_dt = datetime.now(CAIRO_TZ)

        if user.mention in logged_in_users:
            login_time = login_timestamps.get(user.mention)
            duration_str = ""
            if login_time:
                delta = now_dt - login_time
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes = remainder // 60
                duration_str = f"{hours}h {minutes}m"

            del logged_in_users[user.mention]
            login_timestamps.pop(user.mention, None)

            await interaction.message.edit(embed=create_attendance_embed())
            await interaction.response.send_message(
                f"Logged out successfully at {current_time} 🛑\n⏱️ Total shift duration: **{duration_str}**",
                ephemeral=True
            )

            # إشعار اللوج روم بالـ Logout + المدة
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="🔴 Staff Logged Out",
                    description=f"{user.mention} ended their shift at **{current_time}**\n⏱️ Total duration: **{duration_str}**",
                    color=discord.Color.red()
                )
                log_embed.set_thumbnail(url=user.display_avatar.url)
                await log_channel.send(embed=log_embed)
        else:
            await interaction.response.send_message("You haven't logged in yet today!", ephemeral=True)


# ============================================================
# الـ Scheduler
# ============================================================
@tasks.loop(minutes=1)
async def auto_attendance_scheduler():
    now = datetime.now(CAIRO_TZ)

    # تفريغ التتبع الأسبوعي - الإثنين 3:30 مساءً
    if now.weekday() == 0 and now.hour == 15 and now.minute == 30:
        clear_weekly_tracker()
        reset_weekly_leaderboard()

    # 1. الساعة 4:00 مساءً - فتح الحضور
    if now.weekday() in [0, 1, 2, 3, 4] and now.hour == 16 and now.minute == 0:
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            logged_in_users.clear()
            login_timestamps.clear()
            await channel.purge(limit=5, check=lambda m: m.author == bot.user)
            await channel.send(embed=create_attendance_embed(), view=AttendanceView())
            await asyncio.sleep(60)

    # 2. الساعة 4:40 مساءً - التارجت اليومي لكل موظف
    if now.weekday() in [0, 1, 2, 3, 4] and now.hour == 16 and now.minute == 40:
        channel = bot.get_channel(CHANNEL_ID)
        log_channel = bot.get_channel(LOG_CHANNEL_ID)

        if channel and log_channel:
            guild = channel.guild
            role = guild.get_role(STAFF_ROLE_ID)

            if role:
                sent_to = []
                failed = []
                for member in role.members:
                    if member.bot:
                        continue
                    try:
                        target_embed = discord.Embed(
                            title="🎯 Your Daily Target",
                            description=(
                                f"Hey {member.name}! 💪 Here's your mission for today:\n\n"
                                f"**📋 Target:** Get **4 people** to fill out the recruitment form today.\n\n"
                                f"**📌 How to complete it:**\n"
                                f"1️⃣ Reach out to candidates and share the form link.\n"
                                f"2️⃣ Make sure they **fully complete** the form.\n"
                                f"3️⃣ Take a **screenshot** of each submitted form as proof.\n"
                                f"4️⃣ Send all screenshots to your supervisor's DM.\n\n"
                                f"🔥 Let's crush it today! You got this!"
                            ),
                            color=discord.Color.orange()
                        )
                        target_embed.set_footer(text="FireHire Recruitment | Daily Target System")
                        await member.send(embed=target_embed)
                        sent_to.append(member.mention)
                    except discord.Forbidden:
                        failed.append(member.mention)

                # تقرير في اللوج روم
                report = discord.Embed(
                    title="📊 Daily Target DM Report — 4:40 PM",
                    color=discord.Color.orange()
                )
                if sent_to:
                    report.add_field(name="✅ Target Sent To:", value=", ".join(sent_to), inline=False)
                if failed:
                    report.add_field(name="⚠️ Failed (DMs Closed):", value=", ".join(failed), inline=False)
                await log_channel.send(embed=report)

    # 3. الساعة 7:00 مساءً - تذكير متابعة الـ CRM
    if now.weekday() in [0, 1, 2, 3, 4] and now.hour == 19 and now.minute == 0:
        channel = bot.get_channel(CHANNEL_ID)
        log_channel = bot.get_channel(LOG_CHANNEL_ID)

        if channel and log_channel:
            guild = channel.guild
            role = guild.get_role(STAFF_ROLE_ID)

            if role:
                sent_to = []
                failed = []
                for member in role.members:
                    if member.bot:
                        continue
                    try:
                        crm_embed = discord.Embed(
                            title="🔄 CRM Follow-Up Reminder",
                            description=(
                                f"Hey {member.name}! 👋 Time for your **CRM check-in**:\n\n"
                                f"📂 **Go to your CRM now and check:**\n\n"
                                f"✅ **Accepted** — Congratulate them and move to next steps.\n"
                                f"❌ **Rejected** — Update their status and send a polite message.\n"
                                f"⏳ **Still Processing** — Send a follow-up message to check their status.\n\n"
                                f"⚠️ Don't leave anyone without an update!\n"
                                f"🏆 Consistent follow-up = more placements!"
                            ),
                            color=discord.Color.blue()
                        )
                        crm_embed.set_footer(text="FireHire Recruitment | CRM Follow-Up System")
                        await member.send(embed=crm_embed)
                        sent_to.append(member.mention)
                    except discord.Forbidden:
                        failed.append(member.mention)

                # تقرير في اللوج روم
                report = discord.Embed(
                    title="📊 CRM Reminder Report — 7:00 PM",
                    color=discord.Color.blue()
                )
                if sent_to:
                    report.add_field(name="✅ Reminder Sent To:", value=", ".join(sent_to), inline=False)
                if failed:
                    report.add_field(name="⚠️ Failed (DMs Closed):", value=", ".join(failed), inline=False)
                await log_channel.send(embed=report)

    # 4. الساعة 8:30 مساءً - تذكير نهاية اليوم + التارجت
    if now.weekday() in [0, 1, 2, 3, 4] and now.hour == 20 and now.minute == 30:
        channel = bot.get_channel(CHANNEL_ID)
        log_channel = bot.get_channel(LOG_CHANNEL_ID)

        if channel and log_channel:
            guild = channel.guild
            role = guild.get_role(STAFF_ROLE_ID)

            if role:
                sent_to = []
                failed = []
                for member in role.members:
                    if member.bot:
                        continue
                    try:
                        eod_embed = discord.Embed(
                            title="🌙 End of Day Reminder",
                            description=(
                                f"Hey {member.name}! The shift is almost over 🕘\n\n"
                                f"**Before you log out, make sure you've done:**\n\n"
                                f"🎯 Hit your target of **4 form submissions** today?\n"
                                f"📸 Sent all **screenshots** to your supervisor?\n"
                                f"🔄 Updated everyone's status in your **CRM**?\n"
                                f"📋 Followed up with all pending candidates?\n\n"
                                f"✅ If yes — great job today! Log out and rest 💪\n"
                                f"⚠️ If not — you still have time, go finish it!"
                            ),
                            color=discord.Color.purple()
                        )
                        eod_embed.set_footer(text="FireHire Recruitment | End of Day Checklist")
                        await member.send(embed=eod_embed)
                        sent_to.append(member.mention)
                    except discord.Forbidden:
                        failed.append(member.mention)

                report = discord.Embed(
                    title="📊 End of Day Reminder Report — 8:30 PM",
                    color=discord.Color.purple()
                )
                if sent_to:
                    report.add_field(name="✅ Sent To:", value=", ".join(sent_to), inline=False)
                if failed:
                    report.add_field(name="⚠️ Failed (DMs Closed):", value=", ".join(failed), inline=False)
                await log_channel.send(embed=report)

    # 5. الساعة 4:30 مساءً - تذكير المتأخرين + تقرير
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
                    if member.mention not in logged_in_users:
                        try:
                            dm_embed = discord.Embed(
                                title="⚡ Shift Reminder! ⚡",
                                description=f"Hey {member.name},\n\nThe shift started 30 minutes ago! 😉 Don't forget to go to {channel.mention} and click the **Log In 🟢** button. Let's crush it today! 💪🔥",
                                color=discord.Color.orange(),
                            )
                            await member.send(embed=dm_embed)
                            reminded_users.append(member.mention)
                        except discord.Forbidden:
                            closed_dms_users.append(member.mention)

                report_embed = discord.Embed(
                    title="📊 Daily 4:30 PM Reminder Report",
                    color=discord.Color.gold()
                )
                if reminded_users:
                    report_embed.add_field(name="📩 Reminded via DM:", value=", ".join(reminded_users), inline=False)
                if closed_dms_users:
                    report_embed.add_field(name="⚠️ DMs Closed:", value=", ".join(closed_dms_users), inline=False)
                if not reminded_users and not closed_dms_users:
                    report_embed.description = "✅ Perfect! Everyone logged in before 4:30 PM."

                await log_channel.send(embed=report_embed)
            await asyncio.sleep(60)

    # 3. الخميس الساعة 8 مساءً - تحذير قبل الطرد الأسبوعي
    if now.weekday() == 3 and now.hour == 20 and now.minute == 0:
        channel = bot.get_channel(CHANNEL_ID)
        log_channel = bot.get_channel(LOG_CHANNEL_ID)

        if channel and log_channel:
            guild = channel.guild
            role = guild.get_role(STAFF_ROLE_ID)
            weekly_active_ids = load_weekly_tracker()

            if role:
                warned_users = []
                for member in role.members:
                    if member.bot:
                        continue
                    if str(member.id) not in weekly_active_ids:
                        try:
                            warn_embed = discord.Embed(
                                title="⚠️ Final Warning - Inactivity Alert!",
                                description=f"Hey {member.name},\n\n⚠️ You haven't logged in **once** this week!\n\nIf you don't log in **before Friday 9:30 PM**, you will be **automatically removed** from the server.\n\nGo to {channel.mention} and click **Log In 🟢** NOW! 🚨",
                                color=discord.Color.yellow(),
                            )
                            await member.send(embed=warn_embed)
                            warned_users.append(member.mention)
                        except discord.Forbidden:
                            pass

                if warned_users:
                    warn_report = discord.Embed(
                        title="⚠️ Thursday Warning Report",
                        description=f"Sent final kick warning to:\n" + ", ".join(warned_users),
                        color=discord.Color.yellow()
                    )
                    await log_channel.send(embed=warn_report)

    # 4. الساعة 9:30 بالليل - Auto Logout + تقرير اليوم + ليدربورد الجمعة
    if now.weekday() in [0, 1, 2, 3, 4] and now.hour == 21 and now.minute == 30:
        log_channel = bot.get_channel(LOG_CHANNEL_ID)

        if log_channel:
            # Auto Logout لكل اللي نسيوا يعملوا logout
            auto_logouted = []
            now_dt = datetime.now(CAIRO_TZ)
            current_time = now_dt.strftime("%I:%M %p")

            for mention in list(logged_in_users.keys()):
                login_time = login_timestamps.get(mention)
                duration_str = ""
                if login_time:
                    delta = now_dt - login_time
                    hours, remainder = divmod(int(delta.total_seconds()), 3600)
                    minutes = remainder // 60
                    duration_str = f"{hours}h {minutes}m"
                auto_logouted.append(f"• {mention} *(duration: {duration_str})*")
                del logged_in_users[mention]
                login_timestamps.pop(mention, None)

            # تقرير نهاية اليوم
            day_report = discord.Embed(
                title="🌙 End of Shift Report",
                color=discord.Color.blurple()
            )
            if auto_logouted:
                day_report.add_field(
                    name="🔴 Auto Logged Out (forgot to logout):",
                    value="\n".join(auto_logouted),
                    inline=False
                )
            else:
                day_report.add_field(
                    name="✅ All Staff",
                    value="Everyone logged out properly today.",
                    inline=False
                )
            await log_channel.send(embed=day_report)

            # ليدربورد الأسبوع - يتبعت يوم الجمعة بس
            if now.weekday() == 4:
                lb = load_leaderboard()
                channel = bot.get_channel(CHANNEL_ID)
                if lb and channel:
                    guild = channel.guild
                    sorted_lb = sorted(lb.items(), key=lambda x: x[1], reverse=True)
                    medals = ["🥇", "🥈", "🥉"]
                    lb_lines = []
                    for i, (uid, points) in enumerate(sorted_lb):
                        member = guild.get_member(int(uid))
                        name = member.display_name if member else f"User {uid}"
                        medal = medals[i] if i < 3 else f"#{i+1}"
                        lb_lines.append(f"{medal} **{name}** — {points} day(s) this week")

                    lb_embed = discord.Embed(
                        title="🏆 Weekly Attendance Leaderboard",
                        description="\n".join(lb_lines),
                        color=discord.Color.gold()
                    )
                    lb_embed.set_footer(text="See you next week! Keep it up 💪")
                    await log_channel.send(embed=lb_embed)

            await log_channel.send("🌙 Shift ended. Bot is shutting down until tomorrow. Goodnight!")

        await bot.close()
        os._exit(0)

    # 5. الساعة 9:30 يوم الجمعة - طرد المتقاعسين
    if now.weekday() == 4 and now.hour == 21 and now.minute == 30:
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
                    if str(member.id) not in weekly_active_ids:
                        try:
                            await member.send(
                                "You have been removed from the server due to complete inactivity and failure to log in all week. 🛑"
                            )
                            await member.kick(reason="Inactivity - Failed to log in once from Monday to Friday.")
                            kicked_users.append(f"**{member.name}**")
                        except Exception as e:
                            print(f"Could not kick {member.name}: {e}")

                kick_report = discord.Embed(
                    title="🚨 Weekly Inactivity Purge Report",
                    color=discord.Color.red()
                )
                if kicked_users:
                    kick_report.description = "Kicked for zero attendance this week:\n\n" + "\n".join(kicked_users)
                else:
                    kick_report.description = "✅ No one was kicked. All staff logged in at least once!"
                await log_channel.send(embed=kick_report)
            await asyncio.sleep(60)


# ============================================================
# أحداث البوت
# ============================================================
@bot.event
async def on_ready():
    print(f"✅ Bot is online: {bot.user.name}")
    bot.add_view(AttendanceView())
    if not auto_attendance_scheduler.is_running():
        auto_attendance_scheduler.start()


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_attendance(ctx):
    await ctx.message.delete()
    logged_in_users.clear()
    login_timestamps.clear()
    await ctx.send(embed=create_attendance_embed(), view=AttendanceView())


bot.run(DISCORD_TOKEN)
