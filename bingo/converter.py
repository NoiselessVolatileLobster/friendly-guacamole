from typing import List

from redbot.core import commands


class Stamp:
    async def convert(self, ctx: commands.Context, argument: str) -> List[int]:
        if len(argument) != 2:
            raise commands.BadArgument("Your stamp must be exactly 2 characters (e.g., B1, I5).")
        
        letter_part = argument[0].upper()
        
        # 1. Validate the number part (the row)
        try:
            row_number = int(argument[1])
        except ValueError:
            raise commands.BadArgument(f"The second character of your stamp must be a number (1-5), not `{argument[1]}`.")
        
        # 2. Check if the row number is within the 5x5 bounds (1 through 5)
        if not 1 <= row_number <= 5:
            # This check will catch inputs like 'B8'
            raise commands.BadArgument(f"The number part of your stamp (`{row_number}`) must be 1 through 5.")
        
        y = row_number - 1  # Convert 1-5 to 0-4 index for processing
        
        # 3. Validate the letter part (the column)
        bingo = await ctx.cog.config.guild(ctx.guild).bingo()
        try:
            x = bingo.index(letter_part)
        except ValueError:
            raise commands.BadArgument(
                f"`{letter_part}` is not a valid letter in the current bingo sequence (`{bingo}`)."
            )

        # If all checks pass, return the valid coordinates
        return [x, y]