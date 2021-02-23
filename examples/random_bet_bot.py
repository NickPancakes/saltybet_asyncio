#!/usr/bin/env python3

import logging
from argparse import ArgumentParser
from locale import LC_ALL, setlocale
from math import ceil
from pprint import pformat
from random import choice, randint
from saltybet_asyncio.types import Match

import aiorun
from saltybet_asyncio import SideColor, MatchStatus, SaltybetClient

if __name__ == "__main__":

    # Argparse
    parser = ArgumentParser(description="saltybet_asyncio Demo. Will place random bets on a random side every match.")
    parser.add_argument("--email", type=str, help="Saltybet.com Email address")
    parser.add_argument("--password", type=str, help="Saltybet.com Password")
    parser.add_argument("--max-bet", type=int, help="Maximum to randomly bet.")
    parser.add_argument("--min-bet", type=int, help="Minimum to randomly bet.")
    args = parser.parse_args()

    # Logging Config
    setlocale(LC_ALL, "en_US.utf8")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)-12s %(name)-12s: %(levelname)-8s %(message)s",
    )
    logging.getLogger("asyncio").setLevel(logging.ERROR)
    logging.getLogger("socketio").setLevel(logging.ERROR)
    logging.getLogger("engineio").setLevel(logging.ERROR)
    logging.getLogger("aiorun").setLevel(logging.ERROR)
    logger = logging.getLogger(__name__)

    # Setup
    client = SaltybetClient()

    # Bet Detail Storage
    bet_amount = 0
    bet_side = SideColor.UNKNOWN

    # Event Subscriptions
    @client.on_start
    async def login_on_start():
        if args.email and args.password:
            logger.info("Logging In.")
            await client.login(args.email, args.password)
        else:
            logger.info("No credentails provided, no betting will take place.")

    @client.on_betting_open
    async def print_fighters(status: MatchStatus, red_team_name: str, red_bets: int, blue_team_name: str, blue_bets: int):
        global bet_amount
        global bet_side
        logger.info(f"Bets Open: {red_team_name:^16} vs {blue_team_name:^16}")
        if await client.logged_in:
            if await client.illuminati:
                stats = await client.get_match_stats()
                logger.info(f"Illuminati Stats:\n{pformat(stats)}")
            balance = await client.balance
            logger.info(f"Balance: ${balance:n}")
            if args.max_bet and args.min_bet:
                # Bet random amount on a random side
                bet_side = choice([SideColor.RED, SideColor.BLUE])
                bet_amount = randint(args.min_bet, args.max_bet)
                if bet_amount > balance:
                    bet_amount = balance
                await client.place_bet(bet_side, bet_amount)
                fighter_name = red_team_name if bet_side == SideColor.RED else blue_team_name
                logger.info(f"Bet ${bet_amount} on '{fighter_name}' on the {bet_side.name} side.")
        logger.info(f"Bettors:\n{pformat(await client.get_bettors())}")

    @client.on_betting_locked
    async def print_ratio(status: MatchStatus, red_team_name: str, red_bets: int, blue_team_name: str, blue_bets: int):
        logger.info(f"Bets Locked: {red_team_name:>16} - ${red_bets:<16n} vs {blue_team_name:>16} - ${blue_bets:<16n}")
        if red_bets > blue_bets:
            bet_favor = red_bets / blue_bets
            logger.info(f"Bets favor {red_team_name} {bet_favor:.2f}:1 over {blue_team_name}")
        elif blue_bets > red_bets:
            bet_favor = blue_bets / red_bets
            logger.info(f"Bets favor {blue_team_name} {bet_favor:.2f}:1 over {red_team_name}")
        else:
            logger.info(f"Bets are 1:1!")
        logger.info(f"Bettors:\n{pformat(await client.get_bettors())}")

    @client.on_betting_payout
    async def print_win(status: MatchStatus, red_team_name: str, red_bets: int, blue_team_name: str, blue_bets: int):
        global bet_amount
        global bet_side

        won = False
        if status == MatchStatus.RED_WINS:
            winning_team_name = red_team_name
            winning_bets = red_bets
            losing_bets = blue_bets
            if bet_side == SideColor.RED:
                won = True
        elif status == MatchStatus.BLUE_WINS:
            winning_team_name = blue_team_name
            winning_bets = blue_bets
            losing_bets = red_bets
            if bet_side == SideColor.BLUE:
                won = True

        logger.info(f"Match Complete: {winning_team_name} wins!")
        if bet_amount > 0:
            bet_favor = winning_bets / losing_bets
            win_amount = ceil(bet_amount / bet_favor)
            if won:
                logger.info(f"You won ${win_amount}. Nice!")
            else:
                logger.info(f"You're out ${bet_amount}. Dang!")

    aiorun.run(client.run_forever())
