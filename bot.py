import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime
import asyncio
import aiohttp
from dotenv import load_dotenv
from typing import Dict, List, Optional

# --- INITIALIZATION ---

# Load environment variables from the .env file
load_dotenv()

# Bot configuration: Define the intents (permissions) the bot needs.
# Intents are required for the bot to receive certain events from Discord.
intents = discord.Intents.default()
intents.guilds = True  # Required to see server information
intents.members = True  # Required for the on_guild_join welcome message
intents.message_content = True  # Required for text-based commands (e.g., !announce)

# Initialize the Bot object. We use discord.Bot for a slash-command-focused bot.
bot = discord.Bot(intents=intents)

# --- CONFIGURATION & DATA MANAGEMENT ---

# Get owner ID from environment file. This is used for owner-only commands.
# The '0' is a default fallback, but the OWNER_ID should always be set.
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Define file path for persistent server settings.
SETTINGS_FILE = "server_settings.json"

# Program information: This dictionary holds details about your services.
# The 'monitor_name' MUST EXACTLY MATCH the name of the monitor in Uptime Kuma.
PROGRAMS = {
    "quinnflix": {
        "name": "Quinnflix",
        "description": "Media streaming & request service",
        "emoji": "🎬",
        "monitor_name": "Quinnflix",
    },
    "vintage": {
        "name": "Vintage Studio Code",
        "description": "Vintage Story game server",
        "emoji": "🎮",
        "monitor_name": "Vintage Studio Code",
    },
    "sugarcraft": {
        "name": "Sugarcraft",
        "description": "Minecraft server",
        "emoji": "⛏",
        "monitor_name": "Sugarcraft",
    },
}

# Programs a server is enrolled in by default, before it has ever touched
# /programs. Every server running this bot has Quinnflix, so it's on by
# default; everything else is explicit opt-in.
DEFAULT_ENROLLED_PROGRAMS = ["quinnflix"]

# Sentinel for /announce meaning "ignore subscriptions, send to everyone" -- used
# for bot updates and network-wide status that affects every server regardless of
# what they opted into.
#
# This deliberately does NOT live in PROGRAMS. That dict drives the /programs
# toggle panel, /status, update_status_loop, and the welcome embed's server list,
# so a "general" entry there would become a button people could opt out of (which
# defeats the point) and a phantom service that /status reports as permanently
# offline, since Uptime Kuma has no monitor by that name.
GENERAL_ANNOUNCEMENT_KEY = "general"
GENERAL_ANNOUNCEMENT_LABEL = "All Servers"

# Uptime Kuma configuration from environment variables.
UPTIME_KUMA_URL = os.getenv("UPTIME_KUMA_URL", "")
UPTIME_KUMA_STATUS_PAGE_SLUG = os.getenv("UPTIME_KUMA_STATUS_PAGE_SLUG", "default")

# Fixed author info shown on every announcement embed.
ANNOUNCEMENT_AUTHOR_NAME = "kidcorvid"
ANNOUNCEMENT_AUTHOR_ICON_URL = "https://i.imgur.com/qfgL6Pf.png"
ANNOUNCEMENT_AUTHOR_URL = "https://bit.ly/quinnflix"

# Status options for the announcement embed footer/color. Label -> embed color (decimal).
ANNOUNCEMENT_STATUSES = {
    "Normal": 9762148,
    "Incident": 16771191,
    "Info": 3519216,
    "Offline": 14176331,
}
DEFAULT_ANNOUNCEMENT_STATUS = "Normal"

# In-memory cache for program statuses. This is used as a fallback if Uptime Kuma is unreachable.
cached_program_status: Dict[str, str] = {}


def load_settings() -> Dict:
    """Loads server settings from the JSON file into memory."""
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_settings(settings: Dict):
    """Saves the current server settings to the JSON file."""
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)


def ensure_default_program_preferences(guild_id: str) -> None:
    """Gives a server explicit program preferences the first time it's set up.

    Every server running this bot has Quinnflix, so a server that hasn't
    customized its preferences yet is explicitly enrolled in Quinnflix only
    (everything else stays opt-in via /programs). This is written explicitly
    rather than left as an implicit default so /programs shows the correct
    toggle state and people can still opt out of Quinnflix themselves.

    Does nothing if the server already has explicit preferences saved (so it
    never overrides someone's own /programs choices, including opting out of
    Quinnflix).
    """
    settings = server_settings.setdefault(guild_id, {})
    if "programs" not in settings:
        settings["programs"] = list(DEFAULT_ENROLLED_PROGRAMS)


# Load settings on bot startup.
server_settings = load_settings()


# --- HELPER FUNCTIONS ---

def create_welcome_embed() -> discord.Embed:
    """Creates and returns the welcome embed message."""
    welcome_embed = discord.Embed(
        title="🦅 Welcome to the Quinnternet!",
        description="I'm here to deliver announcements about Quinn's servers.",
        color=discord.Color.blurple(),
    )
    welcome_embed.add_field(
        name="🔧 How to Set Me Up",
        value=(
            "1. **DO THIS FIRST:** An admin needs to run `!quinnbotsetup` in the channel you'd like to receive updates & announcements in.\n"
            "2. Admins can use `/programs` to select which updates you're interested in.\n"
            "3. Anyone can use `/status` to check the current status of all programs."
        ),
        inline=False,
    )
    welcome_embed.add_field(
        name="Available Servers",
        value="\n".join(
            [
                f"{info['emoji']} **{info['name']}**: {info['description']}"
                for info in PROGRAMS.values()
            ]
        ),
        inline=False,
    )
    welcome_embed.set_footer(text="nya :3")
    return welcome_embed


# --- UPTIME KUMA INTEGRATION ---


async def fetch_uptime_kuma_status() -> Optional[Dict[str, str]]:
    """
    Fetches the latest status for all monitors from the Uptime Kuma status page.
    Returns a dictionary mapping program_id to its status ('online', 'offline', 'maintenance').
    """
    if not UPTIME_KUMA_URL or not UPTIME_KUMA_STATUS_PAGE_SLUG:
        print("Uptime Kuma URL or Slug is not configured. Skipping status fetch.")
        return None

    # URL for the status page API
    api_url = f"{UPTIME_KUMA_URL}/api/status-page/{UPTIME_KUMA_STATUS_PAGE_SLUG}"
    print(f"Fetching status page API: {api_url}")

    try:
        async with aiohttp.ClientSession() as session:
            # First get the monitor information from the API
            async with session.get(api_url, timeout=10) as response:
                if response.status != 200:
                    print(
                        f"Error: Failed to fetch status page API. HTTP Status: {response.status}"
                    )
                    return None

                try:
                    status_data = await response.json()

                    # Map monitor IDs to names
                    monitor_id_to_name = {}
                    for group in status_data.get("publicGroupList", []):
                        for monitor in group.get("monitorList", []):
                            monitor_id = str(monitor.get("id"))
                            monitor_name = monitor.get("name", "")
                            if monitor_id and monitor_name:
                                monitor_id_to_name[monitor_id] = monitor_name

                    print(f"Found monitors: {monitor_id_to_name}")

                    # Now fetch the heartbeat data
                    heartbeat_url = f"{UPTIME_KUMA_URL}/api/status-page/heartbeat/{UPTIME_KUMA_STATUS_PAGE_SLUG}"
                    print(f"Fetching heartbeat data: {heartbeat_url}")

                    async with session.get(
                        heartbeat_url, timeout=10
                    ) as heartbeat_response:
                        if heartbeat_response.status != 200:
                            print(
                                f"Error: Failed to fetch heartbeat data. HTTP Status: {heartbeat_response.status}"
                            )
                            return {prog_id: "offline" for prog_id in PROGRAMS.keys()}

                        try:
                            heartbeat_data = await heartbeat_response.json()

                            # Extract heartbeat status for each monitor
                            monitor_statuses = {}

                            # Get heartbeats from 'heartbeatList'
                            heartbeat_list = heartbeat_data.get("heartbeatList", {})
                            for monitor_id, beats in heartbeat_list.items():
                                if beats and len(beats) > 0:
                                    # Get the LAST entry (most recent)
                                    latest_beat = beats[-1]
                                    status_value = latest_beat.get("status")

                                    # Map status to our format
                                    if status_value == 1:
                                        status = "online"
                                    elif status_value == 0:
                                        status = "offline"
                                    elif status_value == 3:
                                        status = "maintenance"
                                    else:
                                        status = "offline"

                                    monitor_statuses[monitor_id] = status

                            # Map monitor statuses to our program IDs
                            program_statuses = {}
                            for prog_id, prog_info in PROGRAMS.items():
                                monitor_name = prog_info["monitor_name"]

                                # Find the monitor ID for this program
                                monitor_id = None
                                for m_id, m_name in monitor_id_to_name.items():
                                    if m_name == monitor_name:
                                        monitor_id = m_id
                                        break

                                # Get status for this monitor
                                if monitor_id and monitor_id in monitor_statuses:
                                    program_statuses[prog_id] = monitor_statuses[
                                        monitor_id
                                    ]
                                else:
                                    program_statuses[prog_id] = "offline"

                            return program_statuses
                        except Exception as e:
                            print(f"Error parsing heartbeat data: {e}")
                            return {prog_id: "offline" for prog_id in PROGRAMS.keys()}
                except Exception as e:
                    print(f"Error parsing status page data: {e}")
                    return {prog_id: "offline" for prog_id in PROGRAMS.keys()}
    except Exception as e:
        print(f"An unexpected error occurred while fetching Uptime Kuma status: {e}")
        return {prog_id: "offline" for prog_id in PROGRAMS.keys()}


# --- UI COMPONENTS (for /programs command) ---

# How long the /programs panel stays interactive. Discord measures this from the
# LAST button press, not from when the panel opened, so someone actively toggling
# never hits it -- only an abandoned panel expires.
#
# This must stay comfortably under 15 minutes. The panel is ephemeral, which means
# the bot has no real message object and can only edit it through the interaction
# token, and Discord kills that token 15 minutes after the command is invoked.
# Raise this past ~13 minutes and on_timeout will fire into a dead token, fail,
# and leave the panel looking permanently alive.
PROGRAMS_PANEL_TIMEOUT = 300


def build_programs_embed(guild_id: str, expired: bool = False) -> discord.Embed:
    """Builds the /programs panel embed for a server's current preferences.

    Rebuilt from scratch on every toggle so the embed body always agrees with the
    button colours -- otherwise selection state is only legible to people who know
    green means subscribed.
    """
    settings = server_settings.get(guild_id, {})
    current_prefs = settings.get("programs", list(DEFAULT_ENROLLED_PROGRAMS))

    subscribed = [
        info for prog_id, info in PROGRAMS.items() if prog_id in current_prefs
    ]
    unsubscribed = [
        info for prog_id, info in PROGRAMS.items() if prog_id not in current_prefs
    ]

    embed = discord.Embed(
        title="📋 Program subscription" + (" · expired" if expired else ""),
        description=(
            "This panel timed out. Run `/programs` again to keep editing."
            if expired
            else "Toggle a button to subscribe or unsubscribe. Green means subscribed."
        ),
        color=discord.Color.light_grey() if expired else discord.Color.blue(),
    )

    embed.add_field(
        name="Currently subscribed",
        value="\n".join(f"{i['emoji']} **{i['name']}**" for i in subscribed)
        or "*Nothing yet*",
        inline=False,
    )
    embed.add_field(
        name="Not subscribed",
        value="\n".join(f"{i['emoji']} {i['name']}" for i in unsubscribed)
        or "*Everything is switched on*",
        inline=False,
    )

    # Toggling preferences does nothing visible until an announcement channel
    # exists, so say so plainly rather than letting an admin walk away thinking
    # they finished setting the bot up.
    if not settings.get("channel"):
        embed.add_field(
            name="⚠️ No announcement channel set",
            value=(
                "Your choices are saved, but nothing will be delivered yet. "
                "An admin needs to run `!quinnbotsetup` in the channel that "
                "should receive announcements."
            ),
            inline=False,
        )

    if not expired:
        embed.set_footer(
            text=f"Expires after {PROGRAMS_PANEL_TIMEOUT // 60} minutes of inactivity"
        )

    return embed


class ProgramSelectView(discord.ui.View):
    """A UI View that displays buttons for toggling program announcement preferences."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=PROGRAMS_PANEL_TIMEOUT)
        self.guild_id = str(guild_id)

        # Set by the /programs command after responding. on_timeout needs a handle
        # on the message to grey the panel out, and ctx.respond() doesn't return
        # one for ephemeral responses.
        self.message: Optional[discord.InteractionMessage] = None

        # Get the server's current preferences, defaulting to Quinnflix only if not set.
        current_prefs = server_settings.get(self.guild_id, {}).get(
            "programs", list(DEFAULT_ENROLLED_PROGRAMS)
        )

        # Dynamically create a button for each program
        for program_id, program_info in PROGRAMS.items():
            button = discord.ui.Button(
                label=f"{program_info['name']}",
                emoji=program_info["emoji"],
                custom_id=f"prog_toggle_{program_id}",
            )
            button.callback = self.button_callback
            self.add_item(button)

        self.refresh_buttons(current_prefs)

    def refresh_buttons(self, current_prefs: List[str]) -> None:
        """Repaints every button green or grey to match the saved preferences."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item_prog_id = item.custom_id.replace("prog_toggle_", "")
                item.style = (
                    discord.ButtonStyle.success
                    if item_prog_id in current_prefs
                    else discord.ButtonStyle.secondary
                )

    async def on_timeout(self) -> None:
        """Retires the panel once it has been idle for PROGRAMS_PANEL_TIMEOUT.

        The buttons stop working the moment the view times out, so without this the
        next click just returns Discord's generic 'This interaction failed'. Rather
        than wiping the panel, we disable it in place and keep the subscription list
        visible, so an abandoned panel is still a readable record of what was chosen.
        """
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
                item.style = discord.ButtonStyle.secondary

        if self.message is None:
            return

        try:
            await self.message.edit(
                embed=build_programs_embed(self.guild_id, expired=True), view=self
            )
        except discord.HTTPException:
            # The person dismissed the ephemeral panel themselves, or the
            # interaction token expired. Either way there is nothing left to
            # update and nothing worth logging loudly.
            pass

    async def button_callback(self, interaction: discord.Interaction):
        """This callback handles all button presses in this view."""
        # Ensure the user has permissions to change settings
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "You need 'Manage Channels' permission to change this.", ephemeral=True
            )
            return

        # Extract the program_id from the button's custom_id
        program_id = interaction.data["custom_id"].replace("prog_toggle_", "")

        # Ensure the server has an entry in the settings
        if self.guild_id not in server_settings:
            server_settings[self.guild_id] = {"programs": list(DEFAULT_ENROLLED_PROGRAMS)}

        current_prefs = server_settings[self.guild_id].get(
            "programs", list(DEFAULT_ENROLLED_PROGRAMS)
        )

        # Toggle the preference
        if program_id in current_prefs:
            current_prefs.remove(program_id)
        else:
            current_prefs.append(program_id)

        server_settings[self.guild_id]["programs"] = current_prefs
        save_settings(server_settings)

        # Update the button styles to reflect the new selection
        self.refresh_buttons(current_prefs)

        # Send the embed as well as the view, so the written summary stays in step
        # with the button colours instead of being frozen at whatever it said when
        # the panel first opened.
        await interaction.response.edit_message(
            embed=build_programs_embed(self.guild_id), view=self
        )


# --- BOT EVENTS ---


@bot.event
async def on_ready():
    """Event triggered when the bot successfully connects to Discord."""
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    # Start the background task to update the bot's status
    update_status_loop.start()


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Event triggered when the bot is added to a new server."""
    print(f"Joined new server: {guild.name} (ID: {guild.id})")

    welcome_embed = create_welcome_embed()

    # Try to send the welcome message to the server's system channel or the first available text channel.
    target_channel = guild.system_channel
    if not (target_channel and target_channel.permissions_for(guild.me).send_messages):
        target_channel = next(
            (
                c
                for c in guild.text_channels
                if c.permissions_for(guild.me).send_messages
            ),
            None,
        )

    if target_channel:
        await target_channel.send(embed=welcome_embed)


# --- BACKGROUND TASK ---


@tasks.loop(minutes=1)
async def update_status_loop():
    """A background task that runs every minute to update the bot's presence."""
    global cached_program_status

    # Fetch fresh status from Uptime Kuma
    latest_status = await fetch_uptime_kuma_status()

    # If fetch is successful, update the cache. Otherwise, use the last known status.
    if latest_status:
        cached_program_status = latest_status
    elif not cached_program_status:
        # If fetch fails and cache is empty, set a default unknown status
        cached_program_status = {prog_id: "offline" for prog_id in PROGRAMS.keys()}

    status_emojis = {"online": "✅", "offline": "❌", "maintenance": "🔧"}

    # Create a concise status string for the bot's activity.
    status_parts = []
    for prog_id, status in cached_program_status.items():
        prog_emoji = PROGRAMS[prog_id]["emoji"]
        status_emoji = status_emojis.get(status, "❓")
        status_parts.append(f"{prog_emoji}{status_emoji}")

    status_text = " | ".join(status_parts)

    activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)
    await bot.change_presence(activity=activity)


@update_status_loop.before_loop
async def before_update_status():
    """Ensures the bot is ready before the loop starts."""
    await bot.wait_until_ready()


# --- SLASH COMMANDS (for all users) ---


@bot.slash_command(
    name="status", description="Check the current status of all programs."
)
async def status(ctx: discord.ApplicationContext):
    """Displays the current status of all programs in an embed."""
    await ctx.defer()  # Acknowledge the command immediately, as fetching might take a moment.

    # Use the cached status for a fast response. The background loop keeps it fresh.
    if not cached_program_status:
        await update_status_loop()  # Run once if cache is empty on first command

    status_embed = discord.Embed(
        title="📊 Server Status",
        description="Live status of Quinn's servers.",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow(),
    )

    if UPTIME_KUMA_URL:
        status_embed.add_field(
            name="📈 Live Status Page",
            value=f"[View detailed uptime statistics]({UPTIME_KUMA_URL}/status/{UPTIME_KUMA_STATUS_PAGE_SLUG})",
            inline=False,
        )

    status_map = {
        "online": "✅ Online",
        "offline": "❌ Offline",
        "maintenance": "🔧 Maintenance",
    }

    for prog_id, prog_status in cached_program_status.items():
        prog_info = PROGRAMS[prog_id]
        status_embed.add_field(
            name=f"{prog_info['emoji']} {prog_info['name']}",
            value=status_map.get(prog_status, "❓ Unknown"),
            inline=True,
        )

    await ctx.followup.send(embed=status_embed)


# --- SLASH COMMANDS (for admins) ---


@bot.slash_command(name="setup", description="Set the channel for bot announcements.")
async def setup(
    ctx: discord.ApplicationContext,
    channel: discord.TextChannel = discord.Option(
        description="The channel where announcements will be sent"
    ),
):
    """Sets the announcement channel for the server."""
    if not ctx.author.guild_permissions.manage_channels:
        await ctx.respond(
            "❌ You need the 'Manage Channels' permission to use this command.",
            ephemeral=True,
        )
        return

    guild_id = str(ctx.guild.id)
    if guild_id not in server_settings:
        server_settings[guild_id] = {}

    server_settings[guild_id]["channel"] = channel.id
    ensure_default_program_preferences(guild_id)
    save_settings(server_settings)

    await ctx.respond(
        f"✅ Success! Announcements will now be sent to {channel.mention}.",
        ephemeral=True,
    )


@bot.slash_command(
    name="programs", description="Choose which program announcements to receive."
)
async def programs(ctx: discord.ApplicationContext):
    """Allows admins to configure which program announcements to receive."""
    if not ctx.author.guild_permissions.manage_channels:
        await ctx.respond(
            "❌ You need the 'Manage Channels' permission to use this command.",
            ephemeral=True,
        )
        return

    guild_id = str(ctx.guild.id)
    view = ProgramSelectView(ctx.guild.id)
    await ctx.respond(embed=build_programs_embed(guild_id), view=view, ephemeral=True)

    # ctx.respond() doesn't hand back the message for an ephemeral reply, so fetch
    # the handle separately. on_timeout needs it to grey the panel out later.
    view.message = await ctx.interaction.original_response()


# --- SLASH COMMANDS (for owner) ---


@bot.slash_command(
    name="announce",
    description="Send an announcement to all configured servers (Quinn only).",
    options=[
        discord.Option(
            str, name="message", description="The announcement message", required=True
        ),
        discord.Option(
            str,
            name="title",
            description='Optional: Custom title (defaults to "Announcement")',
            required=False,
        ),
        discord.Option(
            str,
            name="program",
            description="Optional: Program to announce for, or 'general' to reach every server",
            choices=[GENERAL_ANNOUNCEMENT_KEY] + list(PROGRAMS.keys()),
            required=False,
        ),
        discord.Option(
            str,
            name="status",
            description="Optional: Status shown in the footer/embed color (defaults to Normal)",
            choices=list(ANNOUNCEMENT_STATUSES.keys()),
            required=False,
        ),
    ],
)
async def announce(
    ctx: discord.ApplicationContext,
    message: str,
    title: str = None,
    program: str = None,
    status: str = None,
):
    """Sends an announcement to all servers that have set up an announcement channel."""
    if ctx.author.id != OWNER_ID:
        await ctx.respond("❌ This is a Quinn-only command.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    # Resolve title, target program, server name, and status/color for the embed.
    #
    # target_program is None for a general announcement, which is the signal the
    # delivery loop below uses to skip subscription filtering entirely. Anything
    # else resolves to a real program key; an unspecified program still defaults
    # to Quinnflix (every server has it), so filtering is always evaluated
    # against something real.
    is_general = program == GENERAL_ANNOUNCEMENT_KEY
    if is_general:
        target_program = None
        server_name = GENERAL_ANNOUNCEMENT_LABEL
    else:
        target_program = program if program and program in PROGRAMS else "quinnflix"
        server_name = PROGRAMS[target_program]["name"]

    display_title = f"📢 {title.strip()}" if title and title.strip() else "📢 Announcement"
    status_label = status if status in ANNOUNCEMENT_STATUSES else DEFAULT_ANNOUNCEMENT_STATUS

    # Create the announcement embed
    announcement_embed = discord.Embed(
        title=display_title,
        description=message,
        color=discord.Colour(ANNOUNCEMENT_STATUSES[status_label]),
        timestamp=datetime.utcnow(),
    )
    announcement_embed.set_author(
        name=ANNOUNCEMENT_AUTHOR_NAME,
        url=ANNOUNCEMENT_AUTHOR_URL,
        icon_url=ANNOUNCEMENT_AUTHOR_ICON_URL,
    )
    announcement_embed.add_field(name="Server:", value=server_name, inline=True)
    announcement_embed.set_footer(text=f"Status: {status_label}")

    sent_count = 0
    failed_count = 0

    for guild_id, settings in server_settings.items():
        channel_id = settings.get("channel")
        if not channel_id:
            continue

        # Only send to servers subscribed to the target program. A general
        # announcement has no target program and deliberately ignores this,
        # reaching every server that has an announcement channel set.
        if target_program is not None:
            subscribed_programs = settings.get(
                "programs", list(DEFAULT_ENROLLED_PROGRAMS)
            )
            if target_program not in subscribed_programs:
                continue

        try:
            guild = bot.get_guild(int(guild_id))
            if not guild:
                continue

            channel = guild.get_channel(channel_id)
            if channel and channel.permissions_for(guild.me).send_messages:
                await channel.send(embed=announcement_embed)
                sent_count += 1
            else:
                failed_count += 1
        except Exception as e:
            print(f"Failed to send announcement to guild {guild_id}: {e}")
            failed_count += 1

    # Name the scope explicitly so a broadcast is never mistaken for a filtered
    # send that happened to reach a lot of servers.
    if is_general:
        summary = f"✅ Announcement broadcast to all {sent_count} configured servers."
    else:
        summary = (
            f"✅ Announcement sent to {sent_count} servers subscribed to {server_name}."
        )

    await ctx.followup.send(f"{summary} ({failed_count} failures.)")


# --- TEXT-BASED COMMANDS (for owner convenience and admin setup) ---


@bot.event
async def on_message(message: discord.Message):
    """Handles incoming messages to check for owner commands and admin setup."""
    # Ignore messages from the bot itself
    if message.author.bot:
        return

    # Check for the setup command (available to admins)
    if message.content.lower() == "!quinnbotsetup":
        # Check if user has manage_channels permission
        if not message.author.guild_permissions.manage_channels:
            await message.reply(
                "❌ You need the 'Manage Channels' permission to use this command."
            )
            return

        # Save this channel as the announcement channel
        guild_id = str(message.guild.id)
        if guild_id not in server_settings:
            server_settings[guild_id] = {}

        server_settings[guild_id]["channel"] = message.channel.id
        ensure_default_program_preferences(guild_id)
        save_settings(server_settings)

        # Send confirmation embed
        setup_embed = discord.Embed(
            title="✅ Setup Complete!",
            description=f"This channel ({message.channel.mention}) has been set as the announcement channel.",
            color=discord.Color.green(),
        )
        setup_embed.add_field(
            name="📋 Next Steps",
            value="• Use `/programs` to select which server announcements you want to receive\n"
            + "• Use `/status` to check the current status of all programs\n"
            + "• Announcements will now appear in this channel",
            inline=False,
        )
        setup_embed.set_footer(
            text="You can run !quinnbotsetup in a different channel to change the announcement channel."
        )

        await message.reply(embed=setup_embed)
        return

    # Check for the start command (available to admins)
    if message.content.lower() == "!quinnbotstart":
        # Check if user has manage_channels permission
        if not message.author.guild_permissions.manage_channels:
            await message.reply(
                "❌ You need the 'Manage Channels' permission to use this command."
            )
            return

        # Send the welcome message
        welcome_embed = create_welcome_embed()
        await message.channel.send(embed=welcome_embed)
        return


# --- RUN THE BOT ---

if __name__ == "__main__":
    # This is the entry point of the script.
    # It retrieves the token from the environment and starts the bot.
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found in .env file. Bot cannot start.")
    else:
        bot.run(token)