#!/usr/bin/env python3
"""pylint option block-disable"""

import asyncio
from collections.abc import Callable, Awaitable
from decimal import Decimal
import logging
from typing import Generator, List, Tuple, Optional
from random import random
import re

import aiohttp
import aiorun
import pendulum
import socketio
from aiohttp.web import HTTPUnauthorized
from selectolax.parser import HTMLParser  # pylint: disable=no-name-in-module

from .types import Fighter, Match, Tournament, Upgrade, SideColor, MatchStatus, GameMode, Tier, UpgradeType, Bettors, Bettor


logger = logging.getLogger("saltybet_asyncio")


class FailedToLoadError(BaseException):
    pass


class SaltybetClient:
    def __init__(self):
        # pylint: disable=unsubscriptable-object
        # Connections
        self.session: aiohttp.ClientSession = None
        self.sio: socketio.AsyncClient = None

        # Limit Management
        self._semaphore: asyncio.Semaphore = None
        self._last_req: pendulum.DateTime = pendulum.now().subtract(minutes=1)

        self._tournament_regex = re.compile(r"(.+) - \$(\d*), (.+) - \$(\d*)")

        # State
        self._started: bool = False
        self._logged_in: bool = False
        self._last_login: pendulum.DateTime = pendulum.now().subtract(days=1)
        self._illuminati: bool = False
        self._betting_status: MatchStatus = MatchStatus.UNKNOWN
        self._game_mode: GameMode = GameMode.UNKNOWN
        self._tournament_id: int = 0
        self._match_id: int = 0
        self._red_team_name: str = ""
        self._red_bets: int = 0
        self._blue_team_name: str = ""
        self._blue_bets: int = 0
        self._matches_left_in_mode: int = 0

        # Credentials
        self.email: Optional[str] = None
        self.password: Optional[str] = None

        # Triggers
        self._on_start_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_end_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_betting_change_triggers: List[Callable[[MatchStatus, str, int, str, int], Awaitable[None]]] = []
        self._on_betting_open_triggers: List[Callable[[MatchStatus, str, int, str, int], Awaitable[None]]] = []
        self._on_betting_locked_triggers: List[Callable[[MatchStatus, str, int, str, int], Awaitable[None]]] = []
        self._on_betting_payout_triggers: List[Callable[[MatchStatus, str, int, str, int], Awaitable[None]]] = []
        self._on_mode_change_triggers: List[Callable[[GameMode], Awaitable[None]]] = []
        self._on_mode_tournament_triggers: List[Callable[[GameMode], Awaitable[None]]] = []
        self._on_mode_exhibition_triggers: List[Callable[[GameMode], Awaitable[None]]] = []
        self._on_mode_matchmaking_triggers: List[Callable[[GameMode], Awaitable[None]]] = []

    async def init(self):
        if self._semaphore is None:
            # Create asyncio semaphore to disallow simulateous scraping.
            self._semaphore = asyncio.Semaphore(1)

        if self.session is None:
            # Create aiohttp session
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10, limit_per_host=5))

        if self.sio is None:
            # SocketIO Client
            self.sio = socketio.AsyncClient()
            # Register Websocket Handler
            self.sio.on("message", self._on_message)
            # Websocket Connect
            await self.sio.connect("https://www.saltybet.com:2096")

        # On_Start Event
        if not self._started:
            for f in self._on_start_triggers:
                await f()
            self._started = True

    def _wait_generator(self, factor: float = 1, max_wait: float = 512.0) -> Generator[float, None, None]:
        n = 0
        while True:
            a = (factor * (2 ** n)) + random()
            if a <= max_wait:
                yield a
                n += 1
            else:
                yield max_wait

    # HTTP GET Request with limit message check. Only used for scraping and illuminati-required stats.
    async def _get_html(self, url: str, max_retries: int = 10) -> Optional[bytes]:  # pylint: disable=unsubscriptable-object
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
                    if content is not None and "The maximum number of stats requests has been reached." in content.text(deep=False):
                        wait_after_limit = next(limit_wait_gen)
                        logger.info(f"Maximum requests hit on attempt {i}. Waiting {wait_after_limit} seconds before retrying...")
                        await asyncio.sleep(wait_after_limit)
                        continue

                    out = html
                    break
        return out

    # Async Properties
    @property
    async def logged_in(self) -> bool:
        if self.email is None or self.password is None:
            return False
        elif self._last_login.diff(pendulum.now()).in_minutes() < 30:
            # Store logged in status for 30 minutes.
            return self._logged_in
        logged_in = True
        async with self.session.get("https://www.saltybet.com/") as resp:
            if not resp.ok:
                logger.error(f"Response code {resp.status} from {resp.url}.")
                return False
            html = await resp.read()
            tree = HTMLParser(html)
            # Check for lgoged in
            for node in tree.css(".nav-text > a:nth-child(1) > span:nth-child(1)"):
                if "Sign in" in node.text():
                    logged_in = False
                    break
            # Check for illuminati
            for node in tree.css(".navbar-text > span:nth-child(1)"):
                if "goldtext" in node.attributes["class"]:
                    self._illuminati = True
                    break
        self._logged_in = logged_in
        return logged_in

    @property
    async def illuminati(self) -> bool:
        if not await self.logged_in:
            logger.error("Illuminati status cannot be checked without being logged in.")
            return False
        return self._illuminati

    @property
    async def balance(self) -> int:
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Balance only available when logged in.")
            return 0

        balance = 0

        html = await self._get_html("https://www.saltybet.com/")
        if html is None:
            logger.error("Failed to get balance.")
            return 0

        selector = "#balance"
        for node in HTMLParser(html).css(selector):
            balance = int(node.text().replace(",", ""))

        return balance

    @property
    async def tournament_id(self) -> Optional[int]:  # pylint: disable=unsubscriptable-object
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Tournament ID only available when logged in.")
            return None

        if not await self.illuminati:
            logger.error("Tournament ID only available with illuminati membership.")
            return None

        if self._tournament_id != 0:
            return self._tournament_id

        html = await self._get_html("https://www.saltybet.com/stats?tournamentstats=1&page=1")
        if html is None:
            logger.error("Failed to get Tournament ID")
            return None

        top_result_node = HTMLParser(html).css_first(
            ".leaderboard > tbody:nth-child(2) > tr:nth-child(1) > td:nth-child(1) > a:nth-child(1)"
        )
        if top_result_node is None:
            logger.error("Failed to get Tournament ID")
            return None
        link = top_result_node.attrs["href"]
        self._tournament_id = int(link.split("=")[-1])
        return self._tournament_id

    @property
    async def match_id(self) -> Optional[int]:  # pylint: disable=unsubscriptable-object
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Match ID only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Match ID only available with illuminati membership.")
            return None

        if self._match_id != 0:
            return self._match_id

        tournament_id = await self.tournament_id
        html = await self._get_html(f"https://www.saltybet.com/stats?tournament_id={tournament_id}")
        if html is None:
            logger.error("Failed to get Match ID")
            return None

        tree = HTMLParser(html)
        top_row = tree.css_first(".leaderboard > tbody:nth-child(2) > tr:nth-child(1) > td:nth-child(1) > a:nth-child(1)")
        if top_row is None:
            logger.error("Failed to get Match ID")
            return None
        match_link = top_row.attrs["href"]
        self._match_id = match_link.split("=")[1]
        return self._match_id

    # Properties parsed from state.json
    @property
    async def betting_status(self) -> MatchStatus:
        if self._betting_status == MatchStatus.UNKNOWN:
            await self._get_state(store=True)
        return self._betting_status

    @property
    async def game_mode(self) -> GameMode:
        if self._game_mode == GameMode.UNKNOWN:
            await self._get_state(store=True)
        return self._game_mode

    @property
    async def red_team_name(self) -> str:
        if self._red_team_name == "":
            await self._get_state(store=True)
        return self._red_team_name

    @property
    async def blue_team_name(self) -> str:
        if self._blue_team_name == "":
            await self._get_state(store=True)
        return self._blue_team_name

    @property
    async def red_bets(self) -> int:
        if self._red_bets == 0:
            await self._get_state(store=True)
        return self._red_bets

    @property
    async def blue_bets(self) -> int:
        if self._blue_bets == 0:
            await self._get_state(store=True)
        return self._blue_bets

    # Actions
    async def login(self, email: str, password: str):
        self.email = email
        self.password = password
        await self.init()
        await self._login()

    async def _login(self):
        if self.email is None or self.password is None:
            logger.error("Login Failed, credentials not provided.")
            raise HTTPUnauthorized
        if await self.logged_in:
            return
        data = {"email": self.email, "pword": self.password, "authenticate": "signin"}
        await self.session.post("https://www.saltybet.com/authenticate?signin=1", data=data)
        if not await self.logged_in:
            logger.error("Login Failed, check your credentials.")
            raise HTTPUnauthorized
        self._last_login = pendulum.now()

    async def place_bet(self, side: SideColor, wager: int):
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Placing bets only available when logged in.")
            return
        if wager <= 0:
            return
        balance = await self.balance
        if wager > balance:
            wager = balance
        player = None
        if side == SideColor.RED:
            player = "player1"
        elif side == SideColor.BLUE:
            player = "player2"
        data = {"selectedplayer": player, "wager": wager}
        async with self.session.post("https://www.saltybet.com/ajax_place_bet.php", data=data) as resp:
            text = await resp.text()
            if text == "":
                logger.error("Failed to place bet.")
            else:
                logger.debug("Bet placed successfully")

    async def _get_raw_zdata_json(self) -> Optional[dict]:  # pylint: disable=unsubscriptable-object
        jresp: dict = {}
        async with self.session.get("https://www.saltybet.com/zdata.json") as resp:
            html = await resp.read()
            if html != "":
                jresp = await resp.json(content_type="text/html")
            else:
                return None
        return jresp

    async def get_bettors(self) -> Optional[Bettors]:  # pylint: disable=unsubscriptable-object
        """Fetches data from zdata.json"""
        jresp = await self._get_raw_zdata_json()
        if jresp is None:
            return None
        bettors: Bettors = {"match": {"red_fighters": [], "blue_fighters": []}, "bettors": []}
        bettors["match"]["status"] = self._status_to_MatchStatus(jresp["status"])
        bettors["match"]["red_team_name"] = jresp["p1name"]
        if not jresp["p1name"].startswith("Team "):
            red_fighter: Fighter = {"name": jresp["p1name"]}
            bettors["match"]["red_fighters"] = [red_fighter]
        if not jresp["p2name"].startswith("Team "):
            blue_fighter: Fighter = {"name": jresp["p2name"]}
            bettors["match"]["blue_fighters"] = [blue_fighter]
        bettors["match"]["blue_team_name"] = jresp["p2name"]
        bettors["match"]["red_bets"] = int(jresp["p1total"].replace(",", ""))
        bettors["match"]["blue_bets"] = int(jresp["p2total"].replace(",", ""))
        for k, v in jresp.items():
            if k in ["p1name", "p2name", "p1total", "p2total", "status", "alert", "x", "remaining"]:
                continue
            elif "n" not in v or "b" not in v:
                continue
            bettor: Bettor = {"bettor_id": int(k), "username": v["n"], "balance": int(v["b"])}
            if "p" in v:
                bettor["bet_side"] = SideColor(int(v["p"]))
            if "w" in v:
                bettor["wager"] = int(v["w"])
            if "r" in v:
                if len(v["r"]) > 3:
                    bettor["avatar"] = f"https://www.gravatar.com/avatar/{v['r']}"
                else:
                    bettor["avatar"] = f"https://www.saltybet.com/images/ranksmall/rank{v['r']}.png"
            if "g" in v:
                bettor["illuminati"] = v["g"] == "1"
            if "c" in v and v["c"] != "0" and "," in v["c"]:
                bettor["color_r"], bettor["color_g"], bettor["color_b"] = v["c"].split(",")
            bettors["bettors"].append(bettor)
        return bettors

    async def _get_raw_ajax_get_stats_php(self) -> Optional[dict]:  # pylint: disable=unsubscriptable-object
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Match stats only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Match stats only available with illuminati membership.")
            return None

        jresp: dict = {}
        async with self.session.get("https://www.saltybet.com/ajax_get_stats.php") as resp:
            jresp = await resp.json(content_type="text/html")
        return jresp

    async def get_match_stats(self) -> Optional[Match]:  # pylint: disable=unsubscriptable-object
        """Fetches data from ajax_get_stats.php"""
        jresp = await self._get_raw_ajax_get_stats_php()
        if jresp is None:
            return None
        stats: Match = {"red_fighters": [], "blue_fighters": []}
        red_1: Fighter = {}
        if " / " in jresp["p1tier"]:
            red_2: Fighter = {}
            red_1["name"], red_2["name"] = jresp["p1name"].split(" / ")
            red_1["author"], red_2["author"] = jresp["p1author"].split(" / ")
            red_1["tier"], red_2["tier"] = [Tier[tier] for tier in jresp["p1tier"].split(" / ")]
            red_1["life"], red_2["life"] = jresp["p1life"].split(" / ")
            red_1["meter"], red_2["meter"] = jresp["p1meter"].split(" / ")
            red_1["palette"], red_2["palette"] = jresp["p1palette"].split(" / ")
            red_1["total_matches"], red_2["total_matches"] = [int(tm) for tm in jresp["p1totalmatches"].split(" / ")]
            red_1["win_rate"], red_2["win_rate"] = [(Decimal(wr) / Decimal(100)) for wr in jresp["p1winrate"].split(" / ")]
            stats["red_fighters"] = [red_1, red_2]
        else:
            red_1 = {
                "name": jresp["p1name"],
                "author": jresp["p1author"],
                "tier": Tier[jresp["p1tier"]],
                "life": jresp["p1life"],
                "meter": jresp["p1meter"],
                "palette": jresp["p1palette"],
                "total_matches": int(jresp["p1totalmatches"]),
                "win_rate": Decimal(jresp["p1winrate"]) / Decimal(100),
            }
            stats["red_fighters"] = [red_1]

        blue_1: Fighter = {}
        if " / " in jresp["p2tier"]:
            blue_2: Fighter = {}
            blue_1["name"], blue_2["name"] = jresp["p2name"].split(" / ")
            blue_1["author"], blue_2["author"] = jresp["p2author"].split(" / ")
            blue_1["tier"], blue_2["tier"] = [Tier[tier] for tier in jresp["p2tier"].split(" / ")]
            blue_1["life"], blue_2["life"] = jresp["p2life"].split(" / ")
            blue_1["meter"], blue_2["meter"] = jresp["p2meter"].split(" / ")
            blue_1["palette"], blue_2["palette"] = jresp["p2palette"].split(" / ")
            blue_1["total_matches"], blue_2["total_matches"] = [int(tm) for tm in jresp["p2totalmatches"].split(" / ")]
            blue_1["win_rate"], blue_2["win_rate"] = [(Decimal(wr) / Decimal(100)) for wr in jresp["p2winrate"].split(" / ")]
            stats["blue_fighters"] = [blue_1, blue_2]
        else:
            blue_1 = {
                "name": jresp["p2name"],
                "author": jresp["p2author"],
                "tier": Tier[jresp["p2tier"]],
                "life": jresp["p2life"],
                "meter": jresp["p2meter"],
                "palette": jresp["p2palette"],
                "total_matches": int(jresp["p2totalmatches"]),
                "win_rate": Decimal(jresp["p2winrate"]) / Decimal(100),
            }
            stats["blue_fighters"] = [blue_1]
        return stats

    # Scraper Functions
    def _split_tournament_name_and_mode(self, tournament_name: str) -> Tuple[GameMode, str]:
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

    async def scrape_tournament(self, tournament_id: int) -> Optional[Tournament]:  # pylint: disable=unsubscriptable-object
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Tournament scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Tournament scraping only available with illuminati membership.")
            return None

        tournament: Tournament = {"tournament_id": tournament_id, "mode": GameMode.UNKNOWN, "matches": []}
        html = await self._get_html(f"https://www.saltybet.com/stats?tournament_id={tournament_id}")
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
        tournament["mode"], tournament["name"] = self._split_tournament_name_and_mode(tournament_name)

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

    async def scrape_match(self, tournament_id: int, match_id: int) -> Optional[Match]:  # pylint: disable=unsubscriptable-object
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Match scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Match scraping only available with illuminati membership.")
            return None

        html = await self._get_html(f"https://www.saltybet.com/stats?match_id={match_id}")
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

    async def scrape_compendium(self, tier: Tier) -> Optional[List[Fighter]]:  # pylint: disable=unsubscriptable-object
        fighters: List[Fighter] = []
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Compendium scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Compendium scraping only available with illuminati membership.")
            return None

        html = await self._get_html(f"https://www.saltybet.com/compendium?tier={tier.value}")
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

    async def scrape_fighter(self, tier: Tier, fighter_id: int) -> Optional[Fighter]:  # pylint: disable=unsubscriptable-object
        fighter: Fighter = {}
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Compendium scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Compendium scraping only available with illuminati membership.")
            return None

        html = await self._get_html(f"https://www.saltybet.com/compendium?tier={tier.value}&character={fighter_id}")
        if html is None:
            logger.error("Failed to scrape Fighter")
            return None

        tree = HTMLParser(html)
        fighter = {
            "name": tree.css_first(".statname").text(deep=False).strip(),
            "fighter_id": fighter_id,
            "tier": tier,
            "life": int(tree.css_first("table.detailedstats > tbody:nth-child(2) > tr:nth-child(2) > td:nth-child(1)").text()),
            "meter": int(tree.css_first("table.detailedstats > tbody:nth-child(2) > tr:nth-child(2) > td:nth-child(2)").text()),
            "sprite": f"https://www.saltybet.com/images/charanim/{fighter_id}.gif",
            "upgrades": [],
        }

        author = tree.css_first("#basicstats").text(deep=False).strip().replace("by ", "")
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
                    upgrade["value"] = int(pendulum.from_format(action, "MMMM DD, YYYY").format("X"))
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

    def _status_to_MatchStatus(self, status: str) -> MatchStatus:
        # Determine BettingStatus
        out = MatchStatus.UNKNOWN
        if status == "open":
            out = MatchStatus.OPEN
        elif status == "locked":
            out = MatchStatus.LOCKED
        elif status == "1":
            out = MatchStatus.RED_WINS
        elif status == "2":
            out = MatchStatus.BLUE_WINS
        else:
            logger.warn(f"Unhandled status: {status}")
        return out

    # State Parsing
    async def _get_raw_state_json(self) -> dict:
        state = {}
        async with self.session.get("https://www.saltybet.com/state.json") as resp:
            state = await resp.json(content_type="text/html")
        return state

    async def _get_state(self, store=False) -> Match:
        state = await self._get_raw_state_json()

        out: Match = {}

        out["status"] = self._status_to_MatchStatus(state["status"])

        # Determine GameMode
        until_next_mode: int = 0
        out["mode"] = GameMode.UNKNOWN
        if state["alert"] == "Tournament mode start!":
            out["mode"] = GameMode.TOURNAMENT
        elif state["remaining"].endswith("in the bracket!"):
            until_next_mode = int(state["remaining"].split(" ")[0])
            out["mode"] = GameMode.TOURNAMENT
        elif state["alert"] == "Exhibition mode start!":
            out["mode"] = GameMode.EXHIBITION
        elif state["remaining"].endswith("exhibition matches left!"):
            until_next_mode = int(state["remaining"].split(" ")[0])
            out["mode"] = GameMode.EXHIBITION
        elif state["remaining"].endswith("next tournament!"):
            until_next_mode = int(state["remaining"].split(" ")[0])
            out["mode"] = GameMode.MATCHMAKING
        else:
            logger.debug(f"Unhandled alert: {state['alert']}")
        self._matches_left_in_mode = until_next_mode

        p1name = state["p1name"]
        if not p1name.startswith("Team "):
            out["red_fighters"] = [{"name": p1name}]
        out["red_team_name"] = p1name

        p2name = state["p2name"]
        if not p2name.startswith("Team "):
            out["blue_fighters"] = [{"name": p2name}]
        out["blue_team_name"] = p2name

        out["red_bets"] = int(state["p1total"].replace(",", ""))
        out["blue_bets"] = int(state["p2total"].replace(",", ""))

        if store:
            self._betting_status = out["status"]
            self._game_mode = out["mode"]
            self._red_team_name = out["red_team_name"]
            self._blue_team_name = out["blue_team_name"]
            self._red_bets = out["red_bets"]
            self._blue_bets = out["blue_bets"]

        return out

    # SocketIO Connection / Event Handling
    async def _on_message(self):
        """Parses state.json when indicated to do so by websocket"""
        logger.debug("Socket.io Message Received")
        state = await self._get_state()

        # Update only fighters/bets
        self._red_bets = state["red_bets"]
        self._blue_bets = state["blue_bets"]
        self._red_team_name = state["red_team_name"]
        self._blue_team_name = state["blue_team_name"]

        # Fire Triggers
        betting_status = state["status"]
        if betting_status != self._betting_status:
            self._betting_status = betting_status
            logger.debug(f"Current status changed to {betting_status.name}")
            await self._trigger_betting_change(betting_status)

        game_mode = state["mode"]
        if game_mode != self._game_mode:
            self._game_mode = game_mode
            logger.debug(f"Current mode changed to {game_mode.name}")
            await self._trigger_mode_change(game_mode)

    async def _trigger_betting_change(self, match_status: MatchStatus):
        await asyncio.gather(
            *[
                f(match_status, self._red_team_name, self._red_bets, self._blue_team_name, self._blue_bets)
                for f in self._on_betting_change_triggers
            ]
        )
        if match_status == MatchStatus.OPEN:
            await asyncio.gather(
                *[f(match_status, self._red_team_name, 0, self._blue_team_name, 0) for f in self._on_betting_open_triggers]
            )
        elif match_status == MatchStatus.LOCKED:
            await asyncio.gather(
                *[
                    f(match_status, self._red_team_name, self._red_bets, self._blue_team_name, self._blue_bets)
                    for f in self._on_betting_locked_triggers
                ]
            )
        elif match_status in [MatchStatus.RED_WINS, MatchStatus.BLUE_WINS, MatchStatus.DRAW]:
            await asyncio.gather(
                *[
                    f(match_status, self._red_team_name, self._red_bets, self._blue_team_name, self._blue_bets)
                    for f in self._on_betting_payout_triggers
                ]
            )

    async def _trigger_mode_change(self, game_mode: GameMode):
        await asyncio.gather(*[f(game_mode) for f in self._on_mode_change_triggers])
        if game_mode == GameMode.TOURNAMENT:
            await asyncio.gather(*[f(game_mode) for f in self._on_mode_tournament_triggers])
        elif game_mode == GameMode.EXHIBITION:
            await asyncio.gather(*[f(game_mode) for f in self._on_mode_exhibition_triggers])
        elif game_mode == GameMode.MATCHMAKING:
            await asyncio.gather(*[f(game_mode) for f in self._on_mode_matchmaking_triggers])

    # Event Decorators
    def on_start(self, func: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
        if func not in self._on_start_triggers:
            self._on_start_triggers.append(func)
        return func

    def on_end(self, func: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
        if func not in self._on_end_triggers:
            self._on_end_triggers.append(func)
        return func

    def on_betting_change(
        self, func: Callable[[MatchStatus, str, int, str, int], Awaitable[None]]
    ) -> Callable[[MatchStatus, str, int, str, int], Awaitable[None]]:
        if func not in self._on_betting_change_triggers:
            self._on_betting_change_triggers.append(func)
        return func

    def on_betting_open(
        self, func: Callable[[MatchStatus, str, int, str, int], Awaitable[None]]
    ) -> Callable[[MatchStatus, str, int, str, int], Awaitable[None]]:
        if func not in self._on_betting_open_triggers:
            self._on_betting_open_triggers.append(func)
        return func

    def on_betting_locked(
        self, func: Callable[[MatchStatus, str, int, str, int], Awaitable[None]]
    ) -> Callable[[MatchStatus, str, int, str, int], Awaitable[None]]:
        if func not in self._on_betting_locked_triggers:
            self._on_betting_locked_triggers.append(func)
        return func

    def on_betting_payout(
        self, func: Callable[[MatchStatus, str, int, str, int], Awaitable[None]]
    ) -> Callable[[MatchStatus, str, int, str, int], Awaitable[None]]:
        if func not in self._on_betting_payout_triggers:
            self._on_betting_payout_triggers.append(func)
        return func

    def on_mode_change(self, func: Callable[[GameMode], Awaitable[None]]) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_change_triggers:
            self._on_mode_change_triggers.append(func)
        return func

    def on_mode_tournament(self, func: Callable[[GameMode], Awaitable[None]]) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_tournament_triggers:
            self._on_mode_tournament_triggers.append(func)
        return func

    def on_mode_exhibition(self, func: Callable[[GameMode], Awaitable[None]]) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_exhibition_triggers:
            self._on_mode_exhibition_triggers.append(func)
        return func

    def on_mode_matchmaking(self, func: Callable[[GameMode], Awaitable[None]]) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_matchmaking_triggers:
            self._on_mode_matchmaking_triggers.append(func)
        return func

    async def run_forever(self):
        await self.init()

        # Listen for Events
        while True:
            try:
                await self.sio.wait()
            except asyncio.CancelledError:
                await aiorun.shutdown_waits_for(self.shutdown())
                break
            except Exception as e:
                logger.info(f"Encountered error: {e}\nAttempting to restart.")

    async def shutdown(self):
        # On_End Event
        for f in self._on_end_triggers:
            await f()
        logger.info("Closing connections.")
        await self.sio.disconnect()
        await self.session.close()
