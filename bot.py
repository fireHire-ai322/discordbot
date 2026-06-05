import discord
from discord.ext import commands, tasks
from datetime import datetime
import pytz
import asyncio
import os
import aiohttp

# ============================================================
# ⚙️ الإعدادات
# ============================================================
DISCORD_TOKEN   = os.environ["DISCORD_TOKEN"]
CHANNEL_ID      = 1461466432679182684
STAFF_ROLE_ID   = 1461551955909410972
LOG_CHANNEL_ID  = 1511158605875904622

# Google Apps Script URL للتواصل مع Google Sheets
GAS_URL = os.environ.get("GAS_URL", "")

CAIRO_TZ = pytz.timezone("Africa/Cairo")

# ============================================================
# Mapping التيمات - أضف تيمات جديدة هنا بسهولة
# ============================================================
TEAM_MAP = {
    "A": {"tl_role": "TL-A", "rec_role": "Rec-A", "channel": "hussein-team-a"},
    "B": {"tl_role": "TL-B", "rec_role": "Rec-B", "channel": "amir-team-b"},
    "C": {"tl_role": "TL-C", "rec_role": "Rec-C", "channel": "rahma-team-c"},
    "D": {"tl_role": "TL-D", "rec_role": "Rec-D", "channel": "nehal-team-d"},
    "I": {"tl_role": "TL-I", "rec_role": "Rec-I", "channel": "mariamh-team-i"},
    "J": {"tl_role": "TL-J", "rec_role": "Rec-J", "channel": "sayed-team-j"},
    "N": {"tl_role": "TL-N", "rec_role": "Rec-N", "channel": "aya-team-n"},
    "E": {"tl_role": "TL-E", "rec_role": "Rec-E", "channel": "merna-team-e"},
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

logged_in_users  = {}
login_timestamps = {}

# تذكر مين بعت تقرير النهارده عشان منطلبش منه مرتين
daily_reports_sent = set()

# ============================================================
# Tasks done today - عشان مينفذش أي task أكتر من مرة
# ============================================================
tasks_done_today = set()


# ============================================================
# دوال Google Sheets (بدل JSON)
# ============================================================
async def sheets_request(action, data={}):
    """إرسال request للـ GAS"""
    if not GAS_URL:
        return None
    try:
        params = {"action": action, **data}
        async with aiohttp.ClientSession() as session:
            async with session.get(GAS_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return await resp.json(content_type=None)
    except Exception as e:
        print(f"GAS Error: {e}")
        return None

async def load_weekly_tracker():
    result = await sheets_request("getWeeklyAttendance")
    if result and "ids" in result:
        return result["ids"]
    return []

async def save_to_weekly_tracker(user_id_str):
    await sheets_request("saveAttendance", {"userId": user_id_str})

async def clear_weekly_tracker():
    await sheets_request("clearWeeklyAttendance")

async def load_leaderboard():
    result = await sheets_request("getLeaderboard")
    if result and "data" in result:
        return result["data"]
    return {}

async def save_leaderboard_point(user_id_str):
    await sheets_request("addLeaderboardPoint", {"userId": user_id_str})

async def reset_leaderboard():
    await sheets_request("resetLeaderboard")


# ============================================================
# دوال مساعدة للتيمات
# ============================================================
def get_team_letter_for_member(member):
    """يرجع حرف التيم بناءً على الـ Role"""
    role_names = [r.name for r in member.roles]
    for letter, info in TEAM_MAP.items():
        if info["rec_role"] in role_names or info["tl_role"] in role_names:
            return letter
    return None

def get_tl_for_team(guild, letter):
    """يرجع التيم ليدر بناءً على حرف التيم"""
    if letter not in TEAM_MAP:
        return None
    tl_role_name = TEAM_MAP[letter]["tl_role"]
    tl_role = discord.utils.get(guild.roles, name=tl_role_name)
    if not tl_role:
        return None
    members = [m for m in tl_role.members if not m.bot]
    return members[0] if members else None

def get_team_channel(guild, letter):
    """يرجع قناة التيم"""
    if letter not in TEAM_MAP:
        return None
    channel_name = TEAM_MAP[letter]["channel"]
    return discord.utils.get(guild.text_channels, name=channel_name)

def get_rec_members_for_team(guild, letter):
    """يرجع الريكروترز بتوع تيم معين"""
    if letter not in TEAM_MAP:
        return []
    rec_role_name = TEAM_MAP[letter]["rec_role"]
    rec_role = discord.utils.get(guild.roles, name=rec_role_name)
    if not rec_role:
        return []
    return [m for m in rec_role.members if not m.bot]


# ============================================================
# الـ Embed بتاع الحضور
# ============================================================
def create_attendance_embed():
    embed = discord.Embed(
        title="📝 Daily Attendance System",
        description="Please click the buttons below to manage your shift status for today:",
        color=discord.Color.blue(),
    )
    embed.add_field(name="🟢 Log In",  value="Click when you start your shift.", inline=True)
    embed.add_field(name="🔴 Log Out", value="Click when you finish your shift.", inline=True)

    if logged_in_users:
        users_list = "\n".join(
            [f"🟢 {mention} *(at {time})*" for mention, time in logged_in_users.items()]
        )
    else:
        users_list = "*No one checked in yet.*"

    embed.add_field(name="📊 Active Staff Today", value=users_list, inline=False)
    return embed


# ============================================================
# Modal لتقرير الريكروتر
# ============================================================
class RecruiterReportModal(discord.ui.Modal, title="📋 Daily Report"):
    candidates_count = discord.ui.TextInput(
        label="How many candidates filled the form today?",
        placeholder="e.g. 4",
        required=True,
        max_length=3
    )
    screenshots_sent = discord.ui.TextInput(
        label="Did you send all screenshots? (Yes/No)",
        placeholder="Yes",
        required=True,
        max_length=10
    )
    notes = discord.ui.TextInput(
        label="Any notes or issues today?",
        placeholder="Optional...",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        letter = get_team_letter_for_member(user)
        tl = get_tl_for_team(guild, letter) if letter else None

        # إشعار الريكروتر
        await interaction.response.send_message(
            "✅ Report submitted successfully! Your TL has been notified.", ephemeral=True
        )

        daily_reports_sent.add(str(user.id))

        # إرسال التقرير للتيم ليدر
        if tl:
            now = datetime.now(CAIRO_TZ).strftime("%I:%M %p")
            report_embed = discord.Embed(
                title="📊 Recruiter Daily Report",
                color=discord.Color.green()
            )
            report_embed.add_field(name="👔 Recruiter", value=user.mention, inline=True)
            report_embed.add_field(name="🕐 Submitted At", value=now, inline=True)
            report_embed.add_field(name="\u200B", value="\u200B", inline=False)
            report_embed.add_field(
                name="🎯 Candidates Who Filled Form",
                value=self.candidates_count.value,
                inline=True
            )
            report_embed.add_field(
                name="📸 Screenshots Sent",
                value=self.screenshots_sent.value,
                inline=True
            )
            if self.notes.value:
                report_embed.add_field(
                    name="📝 Notes",
                    value=self.notes.value,
                    inline=False
                )
            report_embed.set_footer(text="FireHire Recruitment | Daily Report System")
            report_embed.set_thumbnail(url=user.display_avatar.url)

            try:
                await tl.send(embed=report_embed)
            except discord.Forbidden:
                pass


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
            await save_to_weekly_tracker(str(user.id))
            await sheets_request("addLeaderboardPoint", {"userId": str(user.id)})

            await interaction.message.edit(embed=create_attendance_embed())
            await interaction.response.send_message(
                f"Logged in successfully at {current_time} 🚀", ephemeral=True
            )

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
                f"Logged out at {current_time} 🛑\n⏱️ Total shift: **{duration_str}**",
                ephemeral=True
            )

            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="🔴 Staff Logged Out",
                    description=f"{user.mention} ended shift at **{current_time}**\n⏱️ Duration: **{duration_str}**",
                    color=discord.Color.red()
                )
                log_embed.set_thumbnail(url=user.display_avatar.url)
                await log_channel.send(embed=log_embed)
        else:
            await interaction.response.send_message("You haven't logged in yet today!", ephemeral=True)


# ============================================================
# Helper: هل الـ task اتنفذت النهارده؟
# ============================================================
def task_done(name: str, now: datetime) -> bool:
    """يرجع True لو الـ task اتنفذت النهارده، ويسجّلها لو لأ"""
    key = f"{name}_{now.date()}"
    if key in tasks_done_today:
        return True
    tasks_done_today.add(key)
    return False


# ============================================================
# الـ Scheduler
# ============================================================
@tasks.loop(minutes=1)
async def auto_attendance_scheduler():
    now = datetime.now(CAIRO_TZ)

    # ============================================================
    # تفريغ التتبع الأسبوعي - الإثنين بين 3:30 و 3:34
    # ============================================================
    if now.weekday() == 0 and now.hour == 15 and 30 <= now.minute < 35:
        if not task_done("clear_weekly", now):
            await clear_weekly_tracker()
            await reset_leaderboard()
            daily_reports_sent.clear()

    # ============================================================
    # 1. الساعة 4:00 مساءً - فتح الحضور (نافذة 5 دقايق)
    # ============================================================
    if now.weekday() in [0,1,2,3,4] and now.hour == 16 and 0 <= now.minute < 5:
        if not task_done("attendance_open", now):
            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                logged_in_users.clear()
                login_timestamps.clear()
                daily_reports_sent.clear()
                await channel.purge(limit=5, check=lambda m: m.author == bot.user)
                await channel.send(embed=create_attendance_embed(), view=AttendanceView())

    # ============================================================
    # 2. الساعة 4:30 مساءً - تذكير المتأخرين (نافذة 5 دقايق)
    # ============================================================
    if now.weekday() in [0,1,2,3,4] and now.hour == 16 and 30 <= now.minute < 35:
        if not task_done("reminder_430", now):
            channel = bot.get_channel(CHANNEL_ID)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel and log_channel:
                guild = channel.guild
                role = guild.get_role(STAFF_ROLE_ID)
                if role:
                    reminded, failed = [], []
                    for member in role.members:
                        if member.bot or member.mention in logged_in_users:
                            continue
                        try:
                            dm_embed = discord.Embed(
                                title="⚡ Shift Reminder!",
                                description=f"Hey {member.name},\n\nShift started 30 mins ago! Go to {channel.mention} and click **Log In 🟢** 💪🔥",
                                color=discord.Color.orange()
                            )
                            await member.send(embed=dm_embed)
                            reminded.append(member.mention)
                        except discord.Forbidden:
                            failed.append(member.mention)

                    report = discord.Embed(title="📊 4:30 PM Reminder Report", color=discord.Color.gold())
                    if reminded:
                        report.add_field(name="📩 Reminded:", value=", ".join(reminded), inline=False)
                    if failed:
                        report.add_field(name="⚠️ DMs Closed:", value=", ".join(failed), inline=False)
                    if not reminded and not failed:
                        report.description = "✅ Everyone logged in before 4:30 PM!"
                    await log_channel.send(embed=report)

    # ============================================================
    # 3. الساعة 4:40 مساءً - التارجت اليومي (نافذة 5 دقايق)
    # ============================================================
    if now.weekday() in [0,1,2,3,4] and now.hour == 16 and 40 <= now.minute < 45:
        if not task_done("daily_target", now):
            channel = bot.get_channel(CHANNEL_ID)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel and log_channel:
                guild = channel.guild
                role = guild.get_role(STAFF_ROLE_ID)
                if role:
                    sent, failed = [], []
                    for member in role.members:
                        if member.bot:
                            continue
                        try:
                            embed = discord.Embed(
                                title="🎯 Your Daily Target",
                                description=(
                                    f"Hey {member.name}! 💪\n\n"
                                    f"**📋 Target:** Get **4 people** to fill the recruitment form today.\n\n"
                                    f"**📌 Steps:**\n"
                                    f"1️⃣ Reach out to candidates & share the form.\n"
                                    f"2️⃣ Make sure they **fully complete** it.\n"
                                    f"3️⃣ Take a **screenshot** of each submission.\n"
                                    f"4️⃣ Send screenshots to your TL's DM.\n\n"
                                    f"🔥 Let's crush it today!"
                                ),
                                color=discord.Color.orange()
                            )
                            embed.set_footer(text="FireHire Recruitment | Daily Target")
                            await member.send(embed=embed)
                            sent.append(member.mention)
                        except discord.Forbidden:
                            failed.append(member.mention)

                    report = discord.Embed(title="📊 Daily Target Report — 4:40 PM", color=discord.Color.orange())
                    if sent:
                        report.add_field(name="✅ Sent To:", value=", ".join(sent), inline=False)
                    if failed:
                        report.add_field(name="⚠️ Failed:", value=", ".join(failed), inline=False)
                    await log_channel.send(embed=report)

    # ============================================================
    # 4. الساعة 7:00 مساءً - تذكير CRM (نافذة 5 دقايق)
    # ============================================================
    if now.weekday() in [0,1,2,3,4] and now.hour == 19 and 0 <= now.minute < 5:
        if not task_done("crm_reminder", now):
            channel = bot.get_channel(CHANNEL_ID)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel and log_channel:
                guild = channel.guild
                role = guild.get_role(STAFF_ROLE_ID)
                if role:
                    sent, failed = [], []
                    for member in role.members:
                        if member.bot:
                            continue
                        try:
                            embed = discord.Embed(
                                title="🔄 CRM Follow-Up Reminder",
                                description=(
                                    f"Hey {member.name}! 👋 Time for your **CRM check-in**:\n\n"
                                    f"✅ **Accepted** — Congratulate & move to next steps.\n"
                                    f"❌ **Rejected** — Update status & send a polite message.\n"
                                    f"⏳ **Still Processing** — Follow up & check status.\n\n"
                                    f"⚠️ Don't leave anyone without an update!\n"
                                    f"🏆 Consistent follow-up = more placements!"
                                ),
                                color=discord.Color.blue()
                            )
                            embed.set_footer(text="FireHire Recruitment | CRM Follow-Up")
                            await member.send(embed=embed)
                            sent.append(member.mention)
                        except discord.Forbidden:
                            failed.append(member.mention)

                    report = discord.Embed(title="📊 CRM Reminder Report — 7:00 PM", color=discord.Color.blue())
                    if sent:
                        report.add_field(name="✅ Sent:", value=", ".join(sent), inline=False)
                    if failed:
                        report.add_field(name="⚠️ Failed:", value=", ".join(failed), inline=False)
                    await log_channel.send(embed=report)

    # ============================================================
    # 5. الساعة 8:30 مساءً - تذكير نهاية اليوم + طلب التقرير (نافذة 5 دقايق)
    # ============================================================
    if now.weekday() in [0,1,2,3,4] and now.hour == 20 and 30 <= now.minute < 35:
        if not task_done("eod_report", now):
            channel = bot.get_channel(CHANNEL_ID)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel and log_channel:
                guild = channel.guild
                role = guild.get_role(STAFF_ROLE_ID)
                if role:
                    sent, failed = [], []
                    for member in role.members:
                        if member.bot:
                            continue
                        try:
                            embed = discord.Embed(
                                title="🌙 End of Day — Submit Your Report",
                                description=(
                                    f"Hey {member.name}! Shift is almost over 🕘\n\n"
                                    f"**Before logging out, check:**\n"
                                    f"🎯 Hit your **4 form submissions** target?\n"
                                    f"📸 Sent **screenshots** to your TL?\n"
                                    f"🔄 Updated your **CRM** statuses?\n\n"
                                    f"📋 **Click the button below to submit your daily report to your TL!**"
                                ),
                                color=discord.Color.purple()
                            )
                            embed.set_footer(text="FireHire Recruitment | End of Day")
                            view = ReportView()
                            await member.send(embed=embed, view=view)
                            sent.append(member.mention)
                        except discord.Forbidden:
                            failed.append(member.mention)

                    report = discord.Embed(title="📊 End of Day Report — 8:30 PM", color=discord.Color.purple())
                    if sent:
                        report.add_field(name="✅ Sent:", value=", ".join(sent), inline=False)
                    if failed:
                        report.add_field(name="⚠️ Failed:", value=", ".join(failed), inline=False)
                    await log_channel.send(embed=report)

    # ============================================================
    # 6. الخميس 8:00 مساءً - تحذير الطرد الأسبوعي (نافذة 5 دقايق)
    # ============================================================
    if now.weekday() == 3 and now.hour == 20 and 0 <= now.minute < 5:
        if not task_done("thursday_warning", now):
            channel = bot.get_channel(CHANNEL_ID)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel and log_channel:
                guild = channel.guild
                role = guild.get_role(STAFF_ROLE_ID)
                weekly_ids = await load_weekly_tracker()
                if role:
                    warned = []
                    for member in role.members:
                        if member.bot or str(member.id) in weekly_ids:
                            continue
                        try:
                            warn_embed = discord.Embed(
                                title="⚠️ Final Warning - Inactivity Alert!",
                                description=(
                                    f"Hey {member.name},\n\n"
                                    f"⚠️ You haven't logged in **once** this week!\n\n"
                                    f"If you don't log in **before Friday 9:30 PM**, "
                                    f"you will be **automatically removed** from the server.\n\n"
                                    f"Go to {channel.mention} and click **Log In 🟢** NOW! 🚨"
                                ),
                                color=discord.Color.yellow()
                            )
                            await member.send(embed=warn_embed)
                            warned.append(member.mention)
                        except discord.Forbidden:
                            pass

                    if warned:
                        warn_report = discord.Embed(
                            title="⚠️ Thursday Warning Report",
                            description="Sent kick warning to:\n" + ", ".join(warned),
                            color=discord.Color.yellow()
                        )
                        await log_channel.send(embed=warn_report)

    # ============================================================
    # 7. يوم 11 من كل شهر - تحذير TL لو تيمه أقل من 10 (نافذة 5 دقايق)
    # ============================================================
    if now.day == 11 and now.hour == 12 and 0 <= now.minute < 5:
        if not task_done("tl_warning_11", now):
            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                guild = channel.guild
                for letter, info in TEAM_MAP.items():
                    team_channel = get_team_channel(guild, letter)
                    if not team_channel:
                        continue
                    rec_members = get_rec_members_for_team(guild, letter)
                    if len(rec_members) < 10:
                        tl = get_tl_for_team(guild, letter)
                        if tl:
                            try:
                                warn_embed = discord.Embed(
                                    title="⚠️ Team Size Warning",
                                    description=(
                                        f"Hey {tl.name}! 👋\n\n"
                                        f"⚠️ Your team **({info['channel']})** currently has only "
                                        f"**{len(rec_members)} recruiters** — below the minimum of **10**.\n\n"
                                        f"📋 **You have until the 21st of this month** to build your team to 10+.\n\n"
                                        f"If your team stays below 10 by the 21st, "
                                        f"you will be **demoted back to Recruiter** in your team.\n\n"
                                        f"🔥 Start recruiting now! You got this!"
                                    ),
                                    color=discord.Color.orange()
                                )
                                await tl.send(embed=warn_embed)
                            except discord.Forbidden:
                                pass

    # ============================================================
    # 8. يوم 21 من كل شهر - طرد TL لو تيمه لسه أقل من 10 (نافذة 5 دقايق)
    # ============================================================
    if now.day == 21 and now.hour == 12 and 0 <= now.minute < 5:
        if not task_done("tl_demotion_21", now):
            channel = bot.get_channel(CHANNEL_ID)
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if channel and log_channel:
                guild = channel.guild
                demoted_tls = []

                for letter, info in TEAM_MAP.items():
                    rec_members = get_rec_members_for_team(guild, letter)
                    if len(rec_members) >= 10:
                        continue

                    tl = get_tl_for_team(guild, letter)
                    if not tl:
                        continue

                    weekly_ids = await load_weekly_tracker()
                    active_recs = [m for m in rec_members if str(m.id) in weekly_ids]
                    most_active = sorted(active_recs, key=lambda m: str(m.id))

                    tl_general_role = discord.utils.get(guild.roles, name="TL General")
                    tl_specific_role = discord.utils.get(guild.roles, name=info["tl_role"])
                    rec_role = discord.utils.get(guild.roles, name=info["rec_role"])

                    try:
                        if tl_general_role:
                            await tl.remove_roles(tl_general_role)
                        if tl_specific_role:
                            await tl.remove_roles(tl_specific_role)
                        if rec_role:
                            await tl.add_roles(rec_role)

                        await tl.send(
                            f"Hey {tl.name}, your team had less than 10 recruiters by the 21st. "
                            f"You have been demoted back to Recruiter in your team. 🛑"
                        )
                        demoted_tls.append(f"**{tl.name}** (Team {letter}) — {len(rec_members)} recruiters")
                    except Exception as e:
                        print(f"Could not demote {tl.name}: {e}")

                if demoted_tls:
                    demotion_report = discord.Embed(
                        title="📉 Monthly TL Demotion Report — Day 21",
                        description="The following TLs were demoted due to team size < 10:\n\n" + "\n".join(demoted_tls),
                        color=discord.Color.red()
                    )
                    await log_channel.send(embed=demotion_report)
                else:
                    await log_channel.send(embed=discord.Embed(
                        title="✅ Monthly TL Check — Day 21",
                        description="All team leaders have 10+ recruiters. No demotions this month!",
                        color=discord.Color.green()
                    ))

    # ============================================================
    # 9. الساعة 9:30 بالليل - Auto Logout + تقرير + ليدربورد (نافذة 5 دقايق)
    # ============================================================
    if now.weekday() in [0,1,2,3,4] and now.hour == 21 and 30 <= now.minute < 35:
        if not task_done("auto_logout", now):
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                auto_logouted = []
                now_dt = datetime.now(CAIRO_TZ)

                for mention in list(logged_in_users.keys()):
                    login_time = login_timestamps.get(mention)
                    duration_str = ""
                    if login_time:
                        delta = now_dt - login_time
                        h, rem = divmod(int(delta.total_seconds()), 3600)
                        m = rem // 60
                        duration_str = f"{h}h {m}m"
                    auto_logouted.append(f"• {mention} *(duration: {duration_str})*")
                    del logged_in_users[mention]
                    login_timestamps.pop(mention, None)

                day_report = discord.Embed(title="🌙 End of Shift Report", color=discord.Color.blurple())
                if auto_logouted:
                    day_report.add_field(
                        name="🔴 Auto Logged Out:",
                        value="\n".join(auto_logouted),
                        inline=False
                    )
                else:
                    day_report.add_field(name="✅ All Staff", value="Everyone logged out properly.", inline=False)
                await log_channel.send(embed=day_report)

                # ليدربورد الجمعة
                if now.weekday() == 4:
                    lb = await load_leaderboard()
                    channel = bot.get_channel(CHANNEL_ID)
                    if lb and channel:
                        guild = channel.guild
                        sorted_lb = sorted(lb.items(), key=lambda x: x[1], reverse=True)
                        medals = ["🥇", "🥈", "🥉"]
                        lines = []
                        for i, (uid, points) in enumerate(sorted_lb):
                            member = guild.get_member(int(uid))
                            name = member.display_name if member else f"User {uid}"
                            medal = medals[i] if i < 3 else f"#{i+1}"
                            lines.append(f"{medal} **{name}** — {points} day(s)")

                        lb_embed = discord.Embed(
                            title="🏆 Weekly Attendance Leaderboard",
                            description="\n".join(lines),
                            color=discord.Color.gold()
                        )
                        lb_embed.set_footer(text="See you next week! 💪")
                        await log_channel.send(embed=lb_embed)

                await log_channel.send("🌙 Shift ended. Goodnight!")


# ============================================================
# View زرار التقرير
# ============================================================
class ReportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📋 Submit Daily Report",
        style=discord.ButtonStyle.green,
        custom_id="submit_report_button"
    )
    async def submit_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) in daily_reports_sent:
            await interaction.response.send_message(
                "✅ You already submitted your report today!", ephemeral=True
            )
            return
        await interaction.response.send_modal(RecruiterReportModal())


# ============================================================
# أحداث البوت
# ============================================================
@bot.event
async def on_ready():
    print(f"✅ Bot is online: {bot.user.name}")
    bot.add_view(AttendanceView())
    bot.add_view(ReportView())
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
