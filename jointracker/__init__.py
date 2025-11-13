from .jointracker import JoinTracker

def setup(bot):
    """
    The setup function required by Red to load the cog.
    It imports the JoinTracker class and adds it to the bot.
    """
    # Instantiate the cog class and register it with the bot
    bot.add_cog(JoinTracker(bot))