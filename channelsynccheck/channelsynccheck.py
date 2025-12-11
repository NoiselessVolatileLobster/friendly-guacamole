import discord
from redbot.core import commands, checks
from redbot.core.utils.chat_formatting import box, pagify

class ChannelSyncCheck(commands.Cog):
    """
    Checks for channels that are out of sync with their category permissions.
    """

    def __init__(self, bot):
        self.bot = bot

    def _get_perm_diff(self, category, channel):
        """
        Compares overwrites between a category and a channel.
        Returns a list of strings describing the differences.
        """
        cat_overwrites = category.overwrites
        chan_overwrites = channel.overwrites
        
        diffs = []
        
        # Get all roles/members involved in either set of overwrites
        all_targets = set(cat_overwrites.keys()) | set(chan_overwrites.keys())
        
        for target in all_targets:
            # We skip specific user overrides to keep the list cleaner, 
            # unless you specifically want to see user-specific desyncs.
            # To see everything, remove the 'if isinstance' check below.
            if isinstance(target, discord.Member):
                 continue

            cat_perms = cat_overwrites.get(target)
            chan_perms = chan_overwrites.get(target)

            # If completely missing from one or the other
            if cat_perms is None and chan_perms is not None:
                diffs.append(f"• {target.name}: Added in Channel (Not in Category)")
                continue
            elif cat_perms is not None and chan_perms is None:
                diffs.append(f"• {target.name}: Missing in Channel (Present in Category)")
                continue

            # If present in both, check specific permission values
            # iter(PermissionOverwrite) yields (name, value) pairs
            # value can be True (Green Check), False (Red X), or None (Grey Slash)
            
            # Convert to dicts for easier comparison
            c_p_dict = dict(cat_perms)
            ch_p_dict = dict(chan_perms)
            
            target_diffs = []
            
            for perm_name, cat_val in c_p_dict.items():
                chan_val = ch_p_dict.get(perm_name)
                
                if cat_val != chan_val:
                    # Format the value for display
                    def fmt_val(v):
                        return "✅" if v is True else "❌" if v is False else "Nr" # Nr = Neutral/Inherit
                    
                    target_diffs.append(f"{perm_name}: {fmt_val(cat_val)} -> {fmt_val(chan_val)}")

            if target_diffs:
                # Format: @RoleName [ send_messages: Nr -> ✅ ]
                diffs.append(f"• {target.name}: " + ", ".join(target_diffs))
                
        return diffs

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def channelsync(self, ctx):
        """
        Lists specifically WHY channels are out of sync with their category.
        
        Nr = Neutral (Grey Slash)
        ✅ = Allowed (Green Check)
        ❌ = Denied (Red X)
        """
        await ctx.typing()

        output_lines = []
        header = f"{'Location':<30} | {'Differences (Cat -> Chan)'}"
        output_lines.append(header)
        output_lines.append("-" * 75)
        
        any_desync = False

        for category in ctx.guild.categories:
            category_header_added = False
            
            for channel in category.channels:
                if not channel.permissions_synced:
                    any_desync = True
                    
                    # Calculate the differences
                    differences = self._get_perm_diff(category, channel)
                    
                    if not differences:
                        # Sometimes permissions_synced is False but the actual overwrites represent the same logic
                        # or it involves a specific member override we skipped.
                        continue

                    if not category_header_added:
                        output_lines.append(f"[{category.name}]")
                        category_header_added = True
                    
                    c_name = f"#{channel.name}" if isinstance(channel, discord.TextChannel) else f"🔊 {channel.name}"
                    output_lines.append(f"  {c_name}")
                    
                    for diff in differences:
                        output_lines.append(f"    {diff}")
                    
                    output_lines.append("") # Spacer

        if not any_desync:
            await ctx.send("All channels are synced with their categories!")
            return

        full_text = "\n".join(output_lines)

        for page in pagify(full_text):
            await ctx.send(box(page, lang="yaml")) # yaml highlights the lists nicely