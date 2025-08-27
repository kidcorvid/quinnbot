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
intents.members = True # Required for the on_guild_join welcome message
intents.message_content = True # Required for text-based commands (e.g., !announce)

# Initialize the Bot object. We use discord.Bot for a slash-command-focused bot.
bot = discord.Bot(intents=intents)

# --- CONFIGURATION & DATA MANAGEMENT ---

# Get owner ID from environment file. This is used for owner-only commands.
# The '0' is a default fallback, but the OWNER_ID should always be set.
OWNER_ID = int(os.getenv('OWNER_ID', '0'))

# Define file path for persistent server settings.
SETTINGS_FILE = 'server_settings.json'

# Program information: This dictionary holds details about your services.
# The 'monitor_name' MUST EXACTLY MATCH the name of the monitor in Uptime Kuma.
PROGRAMS = {
    'quinnflix': {
        'name': 'Quinnflix',
        'description': 'Media streaming & request service',
        'emoji': '🎬',
        'monitor_name': 'Quinnflix'
    },
    'vintage': {
        'name': 'Vintage Studio Code',
        'description': 'Vintage Story game server',
        'emoji': '🎮',
        'monitor_name': 'Vintage Studio Code'
    },
    'sugarcraft': {
        'name': 'Sugarcraft',
        'description': 'Minecraft server',
        'emoji': '⛏️',
        'monitor_name': 'Sugarcraft'
    }
}

# Uptime Kuma configuration from environment variables.
UPTIME_KUMA_URL = os.getenv('UPTIME_KUMA_URL', '')
UPTIME_KUMA_STATUS_PAGE_SLUG = os.getenv('UPTIME_KUMA_STATUS_PAGE_SLUG', 'default')

# In-memory cache for program statuses. This is used as a fallback if Uptime Kuma is unreachable.
cached_program_status: Dict[str, str] = {}

def load_settings() -> Dict:
    """Loads server settings from the JSON file into memory."""
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_settings(settings: Dict):
    """Saves the current server settings to the JSON file."""
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

# Load settings on bot startup.
server_settings = load_settings()


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
                    print(f"Error: Failed to fetch status page API. HTTP Status: {response.status}")
                    return None
                
                try:
                    status_data = await response.json()
                    
                    # Map monitor IDs to names
                    monitor_id_to_name = {}
                    for group in status_data.get('publicGroupList', []):
                        for monitor in group.get('monitorList', []):
                            monitor_id = str(monitor.get('id'))
                            monitor_name = monitor.get('name', '')
                            if monitor_id and monitor_name:
                                monitor_id_to_name[monitor_id] = monitor_name
                    
                    print(f"Found monitors: {monitor_id_to_name}")
                    
                    # Now fetch the heartbeat data
                    heartbeat_url = f"{UPTIME_KUMA_URL}/api/status-page/heartbeat/{UPTIME_KUMA_STATUS_PAGE_SLUG}"
                    print(f"Fetching heartbeat data: {heartbeat_url}")
                    
                    async with session.get(heartbeat_url, timeout=10) as heartbeat_response:
                        if heartbeat_response.status != 200:
                            print(f"Error: Failed to fetch heartbeat data. HTTP Status: {heartbeat_response.status}")
                            return {prog_id: 'offline' for prog_id in PROGRAMS.keys()}
                        
                        try:
                            heartbeat_data = await heartbeat_response.json()
                            
                            # Extract heartbeat status for each monitor
                            monitor_statuses = {}
                            
                            # Get heartbeats from 'heartbeatList'
                            heartbeat_list = heartbeat_data.get('heartbeatList', {})
                            for monitor_id, beats in heartbeat_list.items():
                                if beats and len(beats) > 0:
                                    # Get the LAST entry (most recent)
                                    latest_beat = beats[-1]
                                    status_value = latest_beat.get('status')
                                    
                                    # Map status to our format
                                    if status_value == 1:
                                        status = 'online'
                                    elif status_value == 0:
                                        status = 'offline'
                                    elif status_value == 2:
                                        status = 'maintenance'
                                    else:
                                        status = 'offline'
                                    
                                    monitor_statuses[monitor_id] = status
                            
                            # Map monitor statuses to our program IDs
                            program_statuses = {}
                            for prog_id, prog_info in PROGRAMS.items():
                                monitor_name = prog_info['monitor_name']
                                
                                # Find the monitor ID for this program
                                monitor_id = None
                                for m_id, m_name in monitor_id_to_name.items():
                                    if m_name == monitor_name:
                                        monitor_id = m_id
                                        break
                                
                                # Get status for this monitor
                                if monitor_id and monitor_id in monitor_statuses:
                                    program_statuses[prog_id] = monitor_statuses[monitor_id]
                                else:
                                    program_statuses[prog_id] = 'offline'
                            
                            return program_statuses
                        except Exception as e:
                            print(f"Error parsing heartbeat data: {e}")
                            return {prog_id: 'offline' for prog_id in PROGRAMS.keys()}
                except Exception as e:
                    print(f"Error parsing status page data: {e}")
                    return {prog_id: 'offline' for prog_id in PROGRAMS.keys()}
    except Exception as e:
        print(f"An unexpected error occurred while fetching Uptime Kuma status: {e}")
        return {prog_id: 'offline' for prog_id in PROGRAMS.keys()}

# --- UI COMPONENTS (for /programs command) ---

class ProgramSelectView(discord.ui.View):
    """A UI View that displays buttons for toggling program announcement preferences."""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300) # View times out after 5 minutes of inactivity
        self.guild_id = str(guild_id)
        
        # Get the server's current preferences, defaulting to all programs if not set.
        current_prefs = server_settings.get(self.guild_id, {}).get('programs', list(PROGRAMS.keys()))
        
        # Dynamically create a button for each program
        for program_id, program_info in PROGRAMS.items():
            is_selected = program_id in current_prefs
            button = discord.ui.Button(
                label=f"{program_info['name']}",
                emoji=program_info['emoji'],
                style=discord.ButtonStyle.success if is_selected else discord.ButtonStyle.secondary,
                custom_id=f"prog_toggle_{program_id}"
            )
            button.callback = self.button_callback
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        """This callback handles all button presses in this view."""
        # Ensure the user has permissions to change settings
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("You need 'Manage Channels' permission to change this.", ephemeral=True)
            return

        # Extract the program_id from the button's custom_id
        program_id = interaction.data['custom_id'].replace('prog_toggle_', '')
        
        # Ensure the server has an entry in the settings
        if self.guild_id not in server_settings:
            server_settings[self.guild_id] = {'programs': list(PROGRAMS.keys())}
        
        current_prefs = server_settings[self.guild_id].get('programs', list(PROGRAMS.keys()))
        
        # Toggle the preference
        if program_id in current_prefs:
            current_prefs.remove(program_id)
        else:
            current_prefs.append(program_id)
        
        server_settings[self.guild_id]['programs'] = current_prefs
        save_settings(server_settings)
        
        # Update the button styles to reflect the new selection
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item_prog_id = item.custom_id.replace('prog_toggle_', '')
                if item_prog_id in current_prefs:
                    item.style = discord.ButtonStyle.success
                else:
                    item.style = discord.ButtonStyle.secondary
        
        # Edit the original message to show the updated view
        await interaction.response.edit_message(view=self)

# --- BOT EVENTS ---

@bot.event
async def on_ready():
    """Event triggered when the bot successfully connects to Discord."""
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    # Start the background task to update the bot's status
    update_status_loop.start()

@bot.event
async def on_guild_join(guild: discord.Guild):
    """Event triggered when the bot is added to a new server."""
    print(f"Joined new server: {guild.name} (ID: {guild.id})")
    
    welcome_embed = discord.Embed(
        title="🐦‍⬛ Welcome to the Quinnternet!",
        description="I'm here to deliver announcements about Quinn's servers.",
        color=discord.Color.blurple()
    )
    welcome_embed.add_field(
        name="🔧 How to Set Me Up",
        value=(
            "1. An admin needs to run the `/setup` command to choose an announcement channel.\n"
            "2. Admins can use `/programs` to select which updates you're interested in.\n"
            "3. Anyone can use `/status` to check the current status of all programs."
        ),
        inline=False
    )
    welcome_embed.add_field(
        name="Available Servers",
        value="\n".join([f"{info['emoji']} **{info['name']}**: {info['description']}" for info in PROGRAMS.values()]),
        inline=False
    )
    welcome_embed.set_footer(text="nya :3")
    
    # Try to send the welcome message to the server's system channel or the first available text channel.
    target_channel = guild.system_channel
    if not (target_channel and target_channel.permissions_for(guild.me).send_messages):
        target_channel = next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
    
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
        cached_program_status = {prog_id: 'offline' for prog_id in PROGRAMS.keys()}

    status_emojis = {'online': '✅', 'offline': '❌', 'maintenance': '🔧'}
    
    # Create a concise status string for the bot's activity.
    status_parts = []
    for prog_id, status in cached_program_status.items():
        prog_emoji = PROGRAMS[prog_id]['emoji']
        status_emoji = status_emojis.get(status, '❓')
        status_parts.append(f"{prog_emoji}{status_emoji}")
        
    status_text = " | ".join(status_parts)
    
    activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)
    await bot.change_presence(activity=activity)

@update_status_loop.before_loop
async def before_update_status():
    """Ensures the bot is ready before the loop starts."""
    await bot.wait_until_ready()

# --- SLASH COMMANDS (for all users) ---

@bot.slash_command(name="status", description="Check the current status of all programs.")
async def status(ctx: discord.ApplicationContext):
    """Displays the current status of all programs in an embed."""
    await ctx.defer() # Acknowledge the command immediately, as fetching might take a moment.
    
    # Use the cached status for a fast response. The background loop keeps it fresh.
    if not cached_program_status:
        await update_status_loop() # Run once if cache is empty on first command

    status_embed = discord.Embed(
        title="📊 Server Status",
        description="Live status of Quinn's servers.",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    
    if UPTIME_KUMA_URL:
        status_embed.add_field(
            name="📈 Live Status Page",
            value=f"[View detailed uptime statistics]({UPTIME_KUMA_URL}/status/{UPTIME_KUMA_STATUS_PAGE_SLUG})",
            inline=False
        )

    status_map = {'online': '✅ Online', 'offline': '❌ Offline', 'maintenance': '🔧 Maintenance'}
    
    for prog_id, prog_status in cached_program_status.items():
        prog_info = PROGRAMS[prog_id]
        status_embed.add_field(
            name=f"{prog_info['emoji']} {prog_info['name']}",
            value=status_map.get(prog_status, '❓ Unknown'),
            inline=True
        )
    
    await ctx.followup.send(embed=status_embed)

# --- SLASH COMMANDS (for admins) ---

@bot.slash_command(name="setup", description="Set the channel for bot announcements.")
async def setup(
    ctx: discord.ApplicationContext,
    channel: discord.TextChannel = discord.Option(description="The channel where announcements will be sent")
):
    """Sets the announcement channel for the server."""
    if not ctx.author.guild_permissions.manage_channels:
        await ctx.respond("❌ You need the 'Manage Channels' permission to use this command.", ephemeral=True)
        return
    
    guild_id = str(ctx.guild.id)
    if guild_id not in server_settings:
        server_settings[guild_id] = {}
    
    server_settings[guild_id]['channel'] = channel.id
    save_settings(server_settings)
    
    await ctx.respond(f"✅ Success! Announcements will now be sent to {channel.mention}.", ephemeral=True)

@bot.slash_command(name="programs", description="Choose which program announcements to receive.")
async def programs(ctx: discord.ApplicationContext):
    """Allows admins to configure which program announcements to receive."""
    if not ctx.author.guild_permissions.manage_channels:
        await ctx.respond("❌ You need the 'Manage Channels' permission to use this command.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📋 Program Subscription",
        description="Click the buttons below to toggle announcements for each program.\nGreen means you are subscribed.",
        color=discord.Color.blue()
    )
    view = ProgramSelectView(ctx.guild.id)
    await ctx.respond(embed=embed, view=view, ephemeral=True)

# --- SLASH COMMANDS (for owner) ---

@bot.slash_command(
    name="announce",
    description="Send an announcement to all configured servers (Quinn only).",
    options=[
        discord.Option(
            str,
            name="message",
            description="The announcement message",
            required=True
        ),
        discord.Option(
            str,
            name="program",
            description="Optional: Announce for a specific program",
            choices=list(PROGRAMS.keys()),
            required=False
        )
    ]
)
async def announce(
    ctx: discord.ApplicationContext,
    message: str,
    program: str = None
):
    """Sends an announcement to all servers that have set up an announcement channel."""
    if ctx.author.id != OWNER_ID:
        await ctx.respond("❌ This is a Quinn-only command.", ephemeral=True)
        return
    
    await ctx.defer(ephemeral=True)
    
    # Create the announcement embed
    embed_title = "📢 Announcement"
    if program and program in PROGRAMS:
        embed_title += f" for {PROGRAMS[program]['name']}"

    announcement_embed = discord.Embed(
        title=embed_title,
        description=message,
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    if program and program in PROGRAMS:
        announcement_embed.set_footer(text=f"{PROGRAMS[program]['emoji']} {PROGRAMS[program]['name']}")

    sent_count = 0
    failed_count = 0
    
    for guild_id, settings in server_settings.items():
        channel_id = settings.get('channel')
        if not channel_id:
            continue
        
        # If a program is specified, check if the server is subscribed to it.
        if program:
            subscribed_programs = settings.get('programs', list(PROGRAMS.keys()))
            if program not in subscribed_programs:
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
    
    await ctx.followup.send(f"✅ Announcement sent to {sent_count} servers. ({failed_count} failures.)")

# --- TEXT-BASED COMMANDS (for owner convenience) ---
# This is an alternative way for the owner to send announcements quickly.

@bot.event
async def on_message(message: discord.Message):
    """Handles incoming messages to check for owner commands."""
    # Ignore messages from the bot itself or from non-owners.
    if message.author.id != OWNER_ID or not message.content.startswith('!'):
        return

    # We don't need bot.process_commands because we are handling it manually.
    # This avoids conflicts with a potential commands.Bot instance.

    parts = message.content.split(' ', 2)
    command = parts[0][1:].lower() # e.g., "!announce" -> "announce"
    
    if command == 'announce':
        # Corrected logic for text-based announcements
        if len(parts) < 2:
            await message.reply("Usage: `!announce <message>` or `!announce <program_id> <message>`")
            return
        
        program_id = None
        announce_message = ""
        
        # Check if the first argument is a valid program ID
        if len(parts) > 2 and parts[1].lower() in PROGRAMS:
            program_id = parts[1].lower()
            announce_message = parts[2]
        else:
            announce_message = message.content[len(command) + 2:] # The rest of the message

        # Create embed
        embed_title = "📢 Announcement"
        if program_id:
            embed_title += f" for {PROGRAMS[program_id]['name']}"

        embed = discord.Embed(
            title=embed_title,
            description=announce_message,
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        if program_id:
            embed.set_footer(text=f"{PROGRAMS[program_id]['emoji']} {PROGRAMS[program_id]['name']}")

        # Send to all configured servers
        sent_count = 0
        failed_count = 0
        
        for guild_id, settings in server_settings.items():
            if 'channel' not in settings:
                continue
            
            # If a program is specified, check subscription
            if program_id:
                prefs = settings.get('programs', list(PROGRAMS.keys()))
                if program_id not in prefs:
                    continue
            
            try:
                guild = bot.get_guild(int(guild_id))
                if not guild:
                    continue
                
                channel = guild.get_channel(settings['channel'])
                if channel and channel.permissions_for(guild.me).send_messages:
                    await channel.send(embed=embed)
                    sent_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                print(f"Failed to send text announcement to guild {guild_id}: {e}")
                failed_count += 1
        
        await message.reply(f"✅ Announcement sent to {sent_count} servers. ({failed_count} failures.)")

# --- RUN THE BOT ---

if __name__ == "__main__":
    # This is the entry point of the script.
    # It retrieves the token from the environment and starts the bot.
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        print("Error: DISCORD_TOKEN not found in .env file. Bot cannot start.")
    else:
        bot.run(token)

