#!/usr/bin/env python3

import asyncio
import logging
import re
from random import random
from typing import Generator, List, Optional, Tuple

import pendulum
from aiohttp.web import HTTPUnauthorized
from selectolax.parser import HTMLParser  # pylint: disable=no-name-in-module

from .base import BasicClient
from .types import (
    Fighter,
    GameMode,
    Match,
    MatchStatus,
    Tier,
    Tournament,
    Upgrade,
    UpgradeType,
)

logger = logging.getLogger(__name__)


class ScraperClient(BasicClient):
    def __init__(self):
        # Limit Management
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)
        self._last_req: pendulum.DateTime = pendulum.now().subtract(minutes=1)

        # Regex
        self._tournament_regex = re.compile(r"(.+) - \$(\d*), (.+) - \$(\d*)")

        # Parent __init__()
        super().__init__()

    async def init(self):
        if not self.initialized:
            if self._semaphore is None:
                # Initialize again to ensure we're on the same loop.
                self._semaphore = asyncio.Semaphore(1)

        # Parent init()
        await super().init()

    # Generates exponentially increasing wait times
    def _wait_generator(
        self, factor: float = 1, max_wait: float = 512.0
    ) -> Generator[float, None, None]:
        n = 0
        while True:
            a = (factor * (2 ** n)) + random()
            if a <= max_wait:
                yield a
                n += 1
            else:
                yield max_wait

    # HTTP GET Request with limit message check. Only used for scraping and illuminati-required stats.
    async def _get_html(self, url: str, max_retries: int = 10) -> Optional[bytes]:
        out = None
        normal_wait_gen = self._wait_generator(factor=7.0, max_wait=90.0)
        limit_wait_gen = self._wait_generator(factor=90.0, max_wait=300.0)
        async with self._semaphore:
            logger.debug(f"Attempting to get {url} without hitting limit...")
            for i in range(max_retries):
                # Delay between each request
                since_last_req = pendulum.now().diff(self._last_req).in_seconds()
                logger.debug(f"{since_last_req} seconds since last request")
                wait = next(normal_wait_gen)
                if since_last_req < wait:
                    wait_secs = wait - since_last_req
                    logger.debug(f"Waiting {wait_secs} seconds before next request...")
                    await asyncio.sleep(wait_secs)

                async with self.session.get(url) as resp:
                    self._last_req = pendulum.now()
                    if not resp.ok:
                        logger.error(f"Response code {resp.status} from {resp.url}.")
                        break

                    html = await resp.read()

                    # Check for limit reached message.
                    content = HTMLParser(html).css_first("#content")
                    if (
                        content is not None
                        and "The maximum number of stats requests has been reached."
                        in content.text(deep=False)
                    ):
                        wait_after_limit = next(limit_wait_gen)
                        logger.info(
                            f"Maximum requests hit on attempt {i}. Waiting {wait_after_limit} seconds before retrying..."
                        )
                        await asyncio.sleep(wait_after_limit)
                        continue

                    out = html
                    break
        return out

    def _split_tournament_name_and_mode(
        self, tournament_name: str
    ) -> Tuple[GameMode, str]:
        EXHIBITIONS_TAG = "(Exhibitions)"
        MATCHMAKING_TAG = "(Matchmaking)"
        tournament_title = ""
        tournament_mode = GameMode.UNKNOWN
        if tournament_name.startswith(EXHIBITIONS_TAG):
            tournament_mode = GameMode.EXHIBITION
            tournament_title = tournament_name.replace(EXHIBITIONS_TAG, "").lstrip()
        elif tournament_name.startswith(MATCHMAKING_TAG):
            tournament_mode = GameMode.MATCHMAKING
            tournament_title = tournament_name.replace(MATCHMAKING_TAG, "").lstrip()
        else:
            tournament_mode = GameMode.TOURNAMENT
            tournament_title = tournament_name.split("Tournament)")[1].lstrip()
        return tournament_mode, tournament_title

    async def scrape_tournament(self, tournament_id: int) -> Optional[Tournament]:
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Tournament scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error(
                "Tournament scraping only available with illuminati membership."
            )
            return None

        tournament: Tournament = {
            "tournament_id": tournament_id,
            "mode": GameMode.UNKNOWN,
            "matches": [],
        }
        html = await self._get_html(
            f"https://www.saltybet.com/stats?tournament_id={tournament_id}"
        )
        if html is None:
            logger.error("Failed to scrape Tournament")
            return None
        tree = HTMLParser(html)

        # Determine if empty
        rows = tree.css(".leaderboard > tbody:nth-child(2) > tr")
        if not rows:
            return None

        # Name and Mode
        tournament_name = tree.css_first("#result > strong:nth-child(1)").text()
        tournament["mode"], tournament["name"] = self._split_tournament_name_and_mode(
            tournament_name
        )

        # Skip if unable to determine game mode from title.
        if tournament["mode"] == GameMode.UNKNOWN:
            return None

        # Matches
        for row in rows:
            match: Match = {
                "mode": GameMode.UNKNOWN,
                "status": MatchStatus.UNKNOWN,
                "tournament_id": tournament_id,
                "red_fighters": [],
                "blue_fighters": [],
                "red_bets": 0,
                "blue_bets": 0,
            }

            row_a = row.css_first("td:nth-child(1) > a:nth-child(1)")
            match["match_id"] = row_a.attrs["href"].split("=")[1]
            match["mode"] = tournament["mode"]

            re_match = self._tournament_regex.match(row_a.text())
            if re_match:
                red_team_name, red_bets, blue_team_name, blue_bets = re_match.groups()
                match["red_team_name"] = red_team_name.strip()
                if not match["red_team_name"].startswith("Team "):
                    match["red_fighters"] = [{"name": match["red_team_name"]}]
                if red_bets != "":
                    match["red_bets"] = int(red_bets)
                match["blue_team_name"] = blue_team_name.strip()
                if not match["blue_team_name"].startswith("Team "):
                    match["blue_fighters"] = [{"name": match["blue_team_name"]}]
                if blue_bets != "":
                    match["blue_bets"] = int(blue_bets)

            row_span = row.css_first("td:nth-child(2) > span:nth-child(1)")
            if row_span is None:
                match["status"] = MatchStatus.DRAW
            elif row_span.attrs["class"] == "redtext":
                match["status"] = MatchStatus.RED_WINS
            elif row_span.attrs["class"] == "bluetext":
                match["status"] = MatchStatus.BLUE_WINS

            tournament["matches"].append(match)
        return tournament

    async def scrape_match(self, tournament_id: int, match_id: int) -> Optional[Match]:
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Match scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Match scraping only available with illuminati membership.")
            return None

        html = await self._get_html(
            f"https://www.saltybet.com/stats?match_id={match_id}"
        )
        if html is None:
            logger.error("Failed to scrape Match")
            return None
        tree = HTMLParser(html)

        # Determine if Empty
        rows = tree.css(".leaderboard > tbody:nth-child(2) > tr")
        if not rows:
            logger.error("Failed to scrape Match")
            return None

        match: Match = {
            "match_id": match_id,
            "mode": GameMode.UNKNOWN,
            "status": MatchStatus.UNKNOWN,
            "tournament_id": tournament_id,
            "red_bets": 0,
            "blue_bets": 0,
        }

        result_node = tree.css_first("#result")
        # Winner
        winner_class = result_node.css_first("span").attrs["class"]
        if "redtext" in winner_class:
            match["status"] = MatchStatus.RED_WINS
        elif "bluetext" in winner_class:
            match["status"] = MatchStatus.BLUE_WINS

        # Title / Fighters
        title = result_node.text(deep=False).strip().replace("Winner:", "")
        red_team, remaining_title = title.split(" vs ")
        match["red_team_name"] = red_team
        if not red_team.startswith("Team "):
            match["red_fighters"] = [{"name": red_team}]
        blue_team, remaining_title = remaining_title.split(" at ")
        match["blue_team_name"] = blue_team
        if not blue_team.startswith("Team "):
            match["blue_fighters"] = [{"name": blue_team}]
        match["mode"], _ = self._split_tournament_name_and_mode(remaining_title)

        # Bets
        for row in rows:
            bet_placed_node = row.css_first("td:nth-child(2)")
            amount = int(bet_placed_node.text().split(" on ")[0])
            color_class = bet_placed_node.css_first("span:nth-child(1)").attrs["class"]
            if "redtext" in color_class:
                match["red_bets"] += amount
            elif "bluetext" in color_class:
                match["blue_bets"] += amount
        return match

    async def scrape_compendium(self, tier: Tier) -> Optional[List[Fighter]]:
        fighters: List[Fighter] = []
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Compendium scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error(
                "Compendium scraping only available with illuminati membership."
            )
            return None

        html = await self._get_html(
            f"https://www.saltybet.com/compendium?tier={tier.value}"
        )
        if html is None:
            logger.error("Failed to scrape Compendium")
            return None
        tree = HTMLParser(html)
        rows = tree.css("#tierlist > li")
        if not rows:
            logger.error("Failed to scrape Compendium")
            return None

        for row in rows:
            fighter_id = row.css_first("a:nth-child(1)").attrs["href"].split("=")[-1]
            fighters.append(
                {"name": row.text(), "fighter_id": fighter_id, "tier": tier,}
            )
        return fighters

    async def scrape_fighter(self, tier: Tier, fighter_id: int) -> Optional[Fighter]:
        fighter: Fighter = {}
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Compendium scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error(
                "Compendium scraping only available with illuminati membership."
            )
            return None

        html = await self._get_html(
            f"https://www.saltybet.com/compendium?tier={tier.value}&character={fighter_id}"
        )
        if html is None:
            logger.error("Failed to scrape Fighter")
            return None

        tree = HTMLParser(html)
        fighter = {
            "name": tree.css_first(".statname").text(deep=False).strip(),
            "fighter_id": fighter_id,
            "tier": tier,
            "life": int(
                tree.css_first(
                    "table.detailedstats > tbody:nth-child(2) > tr:nth-child(2) > td:nth-child(1)"
                ).text()
            ),
            "meter": int(
                tree.css_first(
                    "table.detailedstats > tbody:nth-child(2) > tr:nth-child(2) > td:nth-child(2)"
                ).text()
            ),
            "sprite": f"https://www.saltybet.com/images/charanim/{fighter_id}.gif",
            "upgrades": [],
        }

        author = (
            tree.css_first("#basicstats").text(deep=False).strip().replace("by ", "")
        )
        if author != "":
            fighter["author"] = author

        upgrades_block = tree.css_first("#compendiumright > div:nth-child(7)")
        if upgrades_block is not None:
            for html_line in upgrades_block.html.split("<br>"):
                line = HTMLParser(html_line).text()
                if ":" not in line:
                    continue
                upgrade: Upgrade = {
                    "username": "",
                    "upgrade_type": UpgradeType.UNKNOWN,
                    "value": 0,
                }
                upgrade["username"], action = line.split(":")
                if "unlock" in action or "promote" in action:
                    if "unlock" in action:
                        action = action.replace("unlock on", "").strip()
                        upgrade["upgrade_type"] = UpgradeType.UNLOCK
                    else:
                        action = action.replace("promote on", "").strip()
                        upgrade["upgrade_type"] = UpgradeType.PROMOTE
                    upgrade["value"] = int(
                        pendulum.from_format(action, "MMMM DD, YYYY").format("X")
                    )
                elif "exhib meter +" in action:
                    upgrade["upgrade_type"] = UpgradeType.METER_INCREASE
                    upgrade["value"] = int(action.replace("exhib meter +", "").strip())
                elif "exhib meter -" in action:
                    upgrade["upgrade_type"] = UpgradeType.METER_DECREASE
                    upgrade["value"] = int(action.replace("exhib meter -", "").strip())
                elif "life +" in action:
                    upgrade["upgrade_type"] = UpgradeType.LIFE_INCREASE
                    upgrade["value"] = int(action.replace("life +", "").strip())
                elif "life -" in action:
                    upgrade["upgrade_type"] = UpgradeType.LIFE_DECREASE
                    upgrade["value"] = int(action.replace("life -", "").strip())
                fighter["upgrades"].append(upgrade)

        return fighter
