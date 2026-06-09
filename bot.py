"""
FireHire RS — Discord Attendance Bot
- بيحفظ الـ state في Google Sheets عن طريق GAS
- بيشتغل 5.5 ساعة وبيوقف نفسه
- GitHub Actions بيشغله كل 6 ساعات
"""

import discord
from discord.ext import commands
from datetime import datetime
import pytz
import asyncio
import os
import aiohttp
import json
import time
import logging

# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("FireHireBot")

# ============================================================
# Config
# ============================================================
DISCORD_TOKEN  = os.environ["DISCORD_TOKEN"]
CHANNEL_ID     = 1461466432679182684
STAFF_ROLE_ID  = 1461551955909410972
LOG_CHANNEL_ID = 1511158605875904622
GAS_URL        = os.environ.get("GAS_URL", "")

CAIRO_TZ            = pytz.timezone("Africa/Cairo")
MAX_RUNTIME_MINUTES = int(os.environ.get("BOT_MAX_RUNTIME_MINUTES", "330"))
# ============================================================
# Bot
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


TEAM_MAP = {
    "A": {"tl_role": "TL-A", "rec_role": "Rec-A", "channel": "hussein-team-a"},
    "B": {"tl_role": "TL-B", "rec_role": "Rec-B", "channel": "amir-team-b"},
    "C": {"tl_role": "TL-C", "rec_role": "Rec-C", "channel": "rahma-team-c"},
    "D": {"tl_role": "TL-D", "rec_role": "Rec-D", "channel": "nehal-team-d"},
    "I": {"tl_role": "TL-I", "rec_role": "Rec-I", "channel": "mariamh-team-i"},
    "J": {"tl_role": "TL-J", "rec_role": "Rec-J", "channel": "sayed-team-j"},
    "N": {"tl_role": "TL-N", "rec_role": "Rec-N", "channel": "aya-team-n"},
    "E": {"tl_role": "TL-E", "rec_role": "Rec-E", "channel": "merna-team-e"},
    "G": {"tl_role": "TL-G", "rec_role": "Rec-G", "channel": "dalia-team-g"},
}

TASKS = [
    (15, 30, "pre_attendance_msg", {0,1,2,3,4}),
    (16,  0, "attendance_open",    {0,1,2,3,4}),
    (16, 30, "reminder_430",       {0,1,2,3,4}),
    (16, 40, "daily_target",       {0,1,2,3,4}),
    (19,  0, "crm_reminder",       {0,1,2,3,4}),
    (20,  0, "thursday_warning",   {3}),
    (20, 30, "eod_report",         {0,1,2,3,4}),
    (21, 30, "auto_logout",        {0,1,2,3,4}),
]

# ============================================================
# GAS API — كل التعامل مع Google Sheets هنا
# ============================================================
async def gas_request(action, params={}):
    if not GAS_URL:
        log.warning("GAS_URL not set!")
        return None
    try:
        all_params = {"action": action, **params}
        async with aiohttp.ClientSession() as session:
            async with session.get(GAS_URL, params=all_params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                result = await resp.json(content_type=None)
                log.info(f"GAS [{action}] → {result}")
                return result
    except Exception as e:
        log.error(f"GAS error [{action}]: {e}")
        return None

async def get_weekly_ids():
    result = await gas_request("getWeeklyAttendance")
    if result and "ids" in result:
        return set(str(i) for i in result["ids"])
    return set()

async def save_attendance(user_id):
    await gas_request("saveAttendance", {"userId": str(user_id)})

async def clear_weekly():
    await gas_request("clearWeeklyAttendance")

async def get_leaderboard():
    result = await gas_request("getLeaderboard")
    if result and "data" in result:
        return result["data"]
    return {}

async def add_leaderboard_point(user_id):
    await gas_request("addLeaderboardPoint", {"userId": str(user_id)})

async def reset_leaderboard():
    await gas_request("resetLeaderboard")

# ============================================================
# Local State — بس للحاجات اللي محتاجها في نفس الـ run
# (logged_in_today, login_timestamps, daily_reports_sent, tasks_done)
# ============================================================
STATE_FILE = "last_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "logged_in_today":    {},
        "login_timestamps":   {},
        "daily_reports_sent": [],
        "tasks_done":         []
    }

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_task_done(state, task_key, date_str):
    return f"{task_key}_{date_str}" in state.get("tasks_done", [])

def mark_task_done(state, task_key, date_str):
    key = f"{task_key}_{date_str}"
    if key not in state["tasks_done"]:
        state["tasks_done"].append(key)
    state["tasks_done"] = [t for t in state["tasks_done"] if t.split("_")[-1] >= date_str]

# ============================================================
# Helpers
# ============================================================
def get_team_letter_for_member(member):
    role_names = [r.name for r in member.roles]
    for letter, info in TEAM_MAP.items():
        if info["rec_role"] in role_names or info["tl_role"] in role_names:
            return letter
    return None

def get_tl_for_team(guild, letter):
    if letter not in TEAM_MAP:
        return None
    tl_role = discord.utils.get(guild.roles, name=TEAM_MAP[letter]["tl_role"])
    if not tl_role:
        return None
    members = [m for m in tl_role.members if not m.bot]
    return members[0] if members else None

def create_attendance_embed(logged_in_today):
    embed = discord.Embed(
        title="📝 Daily Attendance System",
        description="Please click the buttons below to manage your shift status for today:",
        color=discord.Color.blue(),
    )
    embed.add_field(name="🟢 Log In",  value="Click when you start your shift.", inline=True)
    embed.add_field(name="🔴 Log Out", value="Click when you finish your shift.", inline=True)
    if logged_in_today:
        users_list = "\n".join([f"🟢 {mention} *(at {t})*" for mention, t in logged_in_today.items()])
    else:
        users_list = "*No one checked in yet.*"
    embed.add_field(name="📊 Active Staff Today", value=users_list, inline=False)
    return embed

# ============================================================
# Modal
# ============================================================
class RecruiterReportModal(discord.ui.Modal, title="📋 Daily Report"):
    candidates_count = discord.ui.TextInput(
        label="How many candidates filled the form today?",
        placeholder="e.g. 4", required=True, max_length=3
    )
    screenshots_sent = discord.ui.TextInput(
        label="Did you send all screenshots? (Yes/No)",
        placeholder="Yes", required=True, max_length=10
    )
    notes = discord.ui.TextInput(
        label="Any notes or issues today?",
        placeholder="Optional...", required=False,
        style=discord.TextStyle.paragraph, max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = load_state()

        main_channel = bot.get_channel(CHANNEL_ID)
        if not main_channel:
            await interaction.followup.send("❌ Error: Can't find the server.", ephemeral=True)
            return

        guild  = main_channel.guild
        member = guild.get_member(interaction.user.id)
        if not member:
            await interaction.followup.send("❌ Error: Can't find your role.", ephemeral=True)
            return

        letter = get_team_letter_for_member(member)
        tl     = get_tl_for_team(guild, letter) if letter else None

        uid = str(interaction.user.id)
        if uid not in state["daily_reports_sent"]:
            state["daily_reports_sent"].append(uid)
            save_state(state)

        if tl:
            now_str = datetime.now(CAIRO_TZ).strftime("%I:%M %p")
            report_embed = discord.Embed(title="📊 Recruiter Daily Report", color=discord.Color.green())
            report_embed.add_field(name="👔 Recruiter",    value=member.mention, inline=True)
            report_embed.add_field(name="🕐 Submitted At", value=now_str, inline=True)
            report_embed.add_field(name="\u200B", value="\u200B", inline=False)
            report_embed.add_field(name="🎯 Candidates",  value=self.candidates_count.value, inline=True)
            report_embed.add_field(name="📸 Screenshots", value=self.screenshots_sent.value, inline=True)
            if self.notes.value:
                report_embed.add_field(name="📝 Notes", value=self.notes.value, inline=False)
            report_embed.set_footer(text="FireHire Recruitment | Daily Report System")
            report_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            try:
                await tl.send(embed=report_embed)
                await interaction.followup.send("✅ Report submitted! Your TL has been notified.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("✅ Report submitted, but couldn't DM your TL.", ephemeral=True)
        else:
            await interaction.followup.send("✅ Report submitted, but couldn't find your TL.", ephemeral=True)

# ============================================================
# Views
# ============================================================
class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Log In 🟢", style=discord.ButtonStyle.green, custom_id="login_button")
    async def login_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state        = load_state()
        user         = interaction.user
        current_time = datetime.now(CAIRO_TZ).strftime("%I:%M %p")
        now_dt       = datetime.now(CAIRO_TZ)

        if user.mention not in state["logged_in_today"]:
            state["logged_in_today"][user.mention]  = current_time
            state["login_timestamps"][user.mention] = now_dt.isoformat()
            save_state(state)

            # حفظ في Google Sheets
            await save_attendance(user.id)
            await add_leaderboard_point(user.id)

            await interaction.message.edit(embed=create_attendance_embed(state["logged_in_today"]))
            await interaction.response.send_message(f"✅ Logged in at {current_time} 🚀", ephemeral=True)

            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="🟢 Staff Logged In",
                    description=f"{user.mention} started shift at **{current_time}**",
                    color=discord.Color.green()
                )
                log_embed.set_thumbnail(url=user.display_avatar.url)
                await log_channel.send(embed=log_embed)
        else:
            await interaction.response.send_message("⚠️ You are already logged in!", ephemeral=True)

    @discord.ui.button(label="Log Out 🔴", style=discord.ButtonStyle.red, custom_id="logout_button")
    async def logout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state        = load_state()
        user         = interaction.user
        current_time = datetime.now(CAIRO_TZ).strftime("%I:%M %p")
        now_dt       = datetime.now(CAIRO_TZ)

        if user.mention in state["logged_in_today"]:
            login_iso    = state["login_timestamps"].get(user.mention)
            duration_str = ""
            if login_iso:
                login_time   = datetime.fromisoformat(login_iso)
                delta        = now_dt - login_time
                h, rem       = divmod(int(delta.total_seconds()), 3600)
                m            = rem // 60
                duration_str = f"{h}h {m}m"

            del state["logged_in_today"][user.mention]
            state["login_timestamps"].pop(user.mention, None)
            save_state(state)

            await interaction.message.edit(embed=create_attendance_embed(state["logged_in_today"]))
            await interaction.response.send_message(
                f"🛑 Logged out at {current_time}\n⏱️ Total shift: **{duration_str}**", ephemeral=True
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
            await interaction.response.send_message("⚠️ You haven't logged in yet!", ephemeral=True)


class ReportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Submit Daily Report", style=discord.ButtonStyle.green, custom_id="submit_report_button")
    async def submit_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = load_state()
        if str(interaction.user.id) in state["daily_reports_sent"]:
            await interaction.response.send_message("✅ Already submitted today!", ephemeral=True)
            return
        await interaction.response.send_modal(RecruiterReportModal())


@bot.event
async def on_ready():
    log.info(f"✅ Bot online: {bot.user}")
    bot.add_view(AttendanceView())
    bot.add_view(ReportView())
    bot.loop.create_task(scheduler_loop())

# ============================================================
# Scheduler
# ============================================================
async def scheduler_loop():
    log.info("⏰ Scheduler started")
    start_time = time.monotonic()

    while True:
        elapsed = (time.monotonic() - start_time) / 60
        if elapsed >= MAX_RUNTIME_MINUTES:
            log.info(f"⏳ Max runtime reached. Closing...")
            await bot.close()
            return

        now     = datetime.now(CAIRO_TZ)
        h       = now.hour
        m       = now.minute
        today   = now.strftime("%Y-%m-%d")
        weekday = now.weekday()
        state   = load_state()

        for task_h, task_m, task_key, weekdays in TASKS:
            if weekdays is not None and weekday not in weekdays:
                continue
            task_time = now.replace(hour=task_h, minute=task_m, second=0, microsecond=0)
            diff_secs = abs((now - task_time).total_seconds())
            if diff_secs > 300:
                continue
            if is_task_done(state, task_key, today):
                continue

            log.info(f"▶️ Running: {task_key}")
            try:
                await run_task(task_key, state, now, weekday, today)
                mark_task_done(state, task_key, today)
                save_state(state)
                log.info(f"✅ Done: {task_key}")
            except Exception as e:
                log.error(f"❌ {task_key} failed: {e}", exc_info=True)

        await asyncio.sleep(30)

# ============================================================
# Tasks
# ============================================================
async def run_task(task_key, state, now, weekday, today):
    channel     = bot.get_channel(CHANNEL_ID)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)

    if task_key == "pre_attendance_msg":
        if channel:
            role  = channel.guild.get_role(STAFF_ROLE_ID)
            ping  = role.mention if role else "@staff"
            embed = discord.Embed(
                title="⏰ Shift Starting Soon!",
                description=(
                    f"{ping}\n\n"
                    f"🔔 Shift starts in **30 minutes** at **4:00 PM**!\n\n"
                    f"Get ready and make sure to click **Log In 🟢** when the attendance opens.\n\n"
                    f"💪 Let's make it a great day!"
                ),
                color=discord.Color.orange()
            )
            embed.set_footer(text="FireHire Recruitment | Attendance Reminder")
            await channel.send(embed=embed)

    elif task_key == "attendance_open":
        if channel:
            state["logged_in_today"]    = {}
            state["login_timestamps"]   = {}
            state["daily_reports_sent"] = []
            save_state(state)
            await channel.purge(limit=5, check=lambda msg: msg.author == bot.user)
            await channel.send(embed=create_attendance_embed({}), view=AttendanceView())

    elif task_key == "reminder_430":
        if channel and log_channel:
            guild = channel.guild
            role  = guild.get_role(STAFF_ROLE_ID)
            if role:
                reminded, failed = [], []
                for member in role.members:
                    if member.bot or member.mention in state["logged_in_today"]:
                        continue
                    try:
                        dm_embed = discord.Embed(
                            title="⚡ Shift Reminder!",
                            description=f"Hey {member.name}! Shift started 30 mins ago!\nGo to {channel.mention} and click **Log In 🟢** 💪🔥",
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

    elif task_key == "daily_target":
        if channel and log_channel:
            guild = channel.guild
            role  = guild.get_role(STAFF_ROLE_ID)
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
                                f"**📋 Target:** Get **4 people** to fill the form today.\n\n"
                                f"1️⃣ Reach out to candidates & share the form.\n"
                                f"2️⃣ Make sure they fully complete it.\n"
                                f"3️⃣ Take a screenshot of each submission.\n"
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

                report = discord.Embed(title="📊 Daily Target — 4:40 PM", color=discord.Color.orange())
                if sent:
                    report.add_field(name="✅ Sent:", value=", ".join(sent), inline=False)
                if failed:
                    report.add_field(name="⚠️ Failed:", value=", ".join(failed), inline=False)
                await log_channel.send(embed=report)

    elif task_key == "crm_reminder":
        if channel and log_channel:
            guild = channel.guild
            role  = guild.get_role(STAFF_ROLE_ID)
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
                                f"⏳ **Processing** — Follow up & check status.\n\n"
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

                report = discord.Embed(title="📊 CRM Reminder — 7:00 PM", color=discord.Color.blue())
                if sent:
                    report.add_field(name="✅ Sent:", value=", ".join(sent), inline=False)
                if failed:
                    report.add_field(name="⚠️ Failed:", value=", ".join(failed), inline=False)
                await log_channel.send(embed=report)

    elif task_key == "thursday_warning":
        if channel and log_channel:
            guild    = channel.guild
            role     = guild.get_role(STAFF_ROLE_ID)
            weekly_ids = await get_weekly_ids()
            if role:
                warned = []
                for member in role.members:
                    if member.bot or str(member.id) in weekly_ids:
                        continue
                    try:
                        warn_embed = discord.Embed(
                            title="⚠️ Final Warning - Inactivity!",
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
                    await log_channel.send(embed=discord.Embed(
                        title="⚠️ Thursday Warning Report",
                        description="Sent kick warning to:\n" + ", ".join(warned),
                        color=discord.Color.yellow()
                    ))

    elif task_key == "eod_report":
        if channel and log_channel:
            guild = channel.guild
            role  = guild.get_role(STAFF_ROLE_ID)
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
                                f"📋 **Click below to submit your daily report!**"
                            ),
                            color=discord.Color.purple()
                        )
                        embed.set_footer(text="FireHire Recruitment | End of Day")
                        await member.send(embed=embed, view=ReportView())
                        sent.append(member.mention)
                    except discord.Forbidden:
                        failed.append(member.mention)

                report = discord.Embed(title="📊 End of Day — 8:30 PM", color=discord.Color.purple())
                if sent:
                    report.add_field(name="✅ Sent:", value=", ".join(sent), inline=False)
                if failed:
                    report.add_field(name="⚠️ Failed:", value=", ".join(failed), inline=False)
                await log_channel.send(embed=report)

    elif task_key == "auto_logout":
        if log_channel:
            guild    = log_channel.guild
            week_str = now.strftime("%Y-W%W")

            auto_logouted = []
            for mention in list(state["logged_in_today"].keys()):
                login_iso    = state["login_timestamps"].get(mention)
                duration_str = ""
                if login_iso:
                    login_time = datetime.fromisoformat(login_iso)
                    delta      = now - login_time
                    hh, rem    = divmod(int(delta.total_seconds()), 3600)
                    mm         = rem // 60
                    duration_str = f"{hh}h {mm}m"
                auto_logouted.append(f"• {mention} *(duration: {duration_str})*")

            state["logged_in_today"]  = {}
            state["login_timestamps"] = {}

            day_report = discord.Embed(title="🌙 End of Shift Report", color=discord.Color.blurple())
            if auto_logouted:
                day_report.add_field(name="🔴 Auto Logged Out:", value="\n".join(auto_logouted), inline=False)
            else:
                day_report.add_field(name="✅ All Staff", value="Everyone logged out properly.", inline=False)
            await log_channel.send(embed=day_report)

            # Weekly attendance warning من Google Sheets
            role       = guild.get_role(STAFF_ROLE_ID)
            weekly_ids = await get_weekly_ids()
            if role:
                less_than_3 = []
                for member in role.members:
                    if member.bot:
                        continue
                    # لو مش في الـ weekly_ids يعني مسجلش حضور الأسبوع ده
                    if str(member.id) not in weekly_ids:
                        less_than_3.append(f"• {member.mention} — 0 days this week")

                if less_than_3:
                    warn_embed = discord.Embed(
                        title="⚠️ Weekly Attendance Warning",
                        description="Staff with no logins this week:\n\n" + "\n".join(less_than_3),
                        color=discord.Color.yellow()
                    )
                    await log_channel.send(embed=warn_embed)

            # الجمعة — ليدربورد + تصفير
            if weekday == 4:
                lb      = await get_leaderboard()
                channel = bot.get_channel(CHANNEL_ID)
                if lb and channel:
                    sorted_lb = sorted(lb.items(), key=lambda x: int(x[1]), reverse=True)
                    medals    = ["🥇", "🥈", "🥉"]
                    lines     = []
                    for i, (uid, points) in enumerate(sorted_lb):
                        member = guild.get_member(int(uid))
                        name   = member.display_name if member else f"User {uid}"
                        medal  = medals[i] if i < 3 else f"#{i+1}"
                        lines.append(f"{medal} **{name}** — {points} day(s)")

                    lb_embed = discord.Embed(
                        title="🏆 Weekly Attendance Leaderboard",
                        description="\n".join(lines) if lines else "No data this week.",
                        color=discord.Color.gold()
                    )
                    lb_embed.set_footer(text="See you next week! 💪")
                    await log_channel.send(embed=lb_embed)

                await clear_weekly()
                await reset_leaderboard()
                state["daily_reports_sent"] = []
                log.info("✅ Weekly data reset (Friday)")

            save_state(state)
            await log_channel.send("🌙 Shift ended. Goodnight!")


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_attendance(ctx):
    await ctx.message.delete()
    state = load_state()
    state["logged_in_today"]  = {}
    state["login_timestamps"] = {}
    save_state(state)
    await ctx.send(embed=create_attendance_embed({}), view=AttendanceView())
# ============================================================
# Run
# ============================================================
bot.run(DISCORD_TOKEN)
