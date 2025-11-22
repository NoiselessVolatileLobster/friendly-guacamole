from typing import List

from redbot.core import commands


class Stamp:
    async def convert(self, ctx: commands.Context, argument: str) -> List[int]:
        if len(argument) > 2:
            raise commands.BadArgument("Your stamp must be 2 characters max.")
        
        # Check if the number part is 1-5 to prevent out-of-bounds error
        try:
            row_number = int(argument[1])
            if not 1 <= row_number <= 5:
                raise commands.BadArgument("The number part of your stamp must be 1 through 5.")
        except (ValueError, IndexError):
            raise commands.BadArgument("The second character of your stamp must be a number from 1 to 5.")
        
        y = row_number - 1
        bingo = await ctx.cog.config.guild(ctx.guild).bingo()
        try:
            x = bingo.index(argument[0].upper())
        except ValueError:
            raise commands.BadArgument(
                f"`{argument[0].upper()}` is not a valid letter in {bingo}."
            )

        return [x, y]