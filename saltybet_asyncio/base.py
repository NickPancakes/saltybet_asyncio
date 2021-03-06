#!/usr/bin/env python3

import logging
from decimal import Decimal
from typing import Optional

import pendulum
from aiohttp import ClientSession, TCPConnector
from aiohttp.web import HTTPUnauthorized
from selectolax.parser import HTMLParser  # pylint: disable=no-name-in-module

from .types import (
    Bettor,
    Bettors,
    Fighter,
    GameMode,
    Match,
    MatchStatus,
    SideColor,
    Tier,
)

logger = logging.getLogger(__name__)


class BasicClient:
    """Handles Saltybet Session Management and Basic Actions"""

    def __init__(self):
        # Initialized
        self.initialized: bool = False

        # Connections
        self.session: ClientSession = None

        # Credentials
        self.email: Optional[str] = None
        self.password: Optional[str] = None

        # State
        self._logged_in: bool = False
        self._last_login: pendulum.DateTime = pendulum.now().subtract(days=1)
        self._illuminati: bool = False
        self._tournament_id: int = 0
        self._match_id: int = 0
        self._match: Match = {"status": MatchStatus.UNKNOWN}
        self._matches_left: int = 0

    # Async Init / Shutdown

    async def init(self):
        if not self.initialized:
            if self.session is None:
                # Create aiohttp session
                self.session = ClientSession(
                    connector=TCPConnector(limit=10, limit_per_host=5)
                )
            self.initialized = True

    async def shutdown(self):
        logger.info("Closing HTTP Session.")
        await self.session.close()

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

        async with self.session.get("https://www.saltybet.com/") as resp:
            html = await resp.read()
            if html is None:
                logger.error("Failed to get balance.")
                return 0

            selector = "#balance"
            for node in HTMLParser(html).css(selector):
                balance = int(node.text().replace(",", ""))

        return balance

    @property
    async def tournament_id(self) -> Optional[int]:
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

        async with self.session.get(
            "https://www.saltybet.com/stats?tournamentstats=1&page=1"
        ) as resp:
            html = await resp.read()
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
    async def match_id(self) -> Optional[int]:
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
        async with self.session.get(
            f"https://www.saltybet.com/stats?tournament_id={tournament_id}"
        ) as resp:
            html = await resp.read()
            if html is None:
                logger.error("Failed to get Match ID")
                return None

            tree = HTMLParser(html)
            top_row = tree.css_first(
                ".leaderboard > tbody:nth-child(2) > tr:nth-child(1) > td:nth-child(1) > a:nth-child(1)"
            )
            if top_row is None:
                logger.error("Failed to get Match ID")
                return None
            match_link = top_row.attrs["href"]
            self._match_id = match_link.split("=")[1]
        return self._match_id

    # Properties parsed from state.json
    @property
    async def match(self) -> Match:
        if self._match["status"] == MatchStatus.UNKNOWN:
            await self.get_state()
        return self._match

    @property
    async def match_status(self) -> MatchStatus:
        if self._match["status"] == MatchStatus.UNKNOWN:
            await self.get_state()
        return self._match["status"]

    @property
    async def matches_remaining(self) -> int:
        if self._match["status"] == MatchStatus.UNKNOWN:
            await self.get_state()
        return self._matches_left

    @property
    async def game_mode(self) -> GameMode:
        if self._match["status"] == MatchStatus.UNKNOWN:
            await self.get_state()
        return self._match["mode"]

    @property
    async def red_team_name(self) -> str:
        if self._match["status"] == MatchStatus.UNKNOWN:
            await self.get_state()
        return self._match["red_team_name"]

    @property
    async def blue_team_name(self) -> str:
        if self._match["status"] == MatchStatus.UNKNOWN:
            await self.get_state()
        return self._match["blue_team_name"]

    @property
    async def red_bets(self) -> int:
        if self._match["status"] == MatchStatus.UNKNOWN:
            await self.get_state()
        return self._match["red_bets"]

    @property
    async def blue_bets(self) -> int:
        if self._match["status"] == MatchStatus.UNKNOWN:
            await self.get_state()
        return self._match["blue_bets"]

    # Private Actions
    async def _login(self):
        if self.email is None or self.password is None:
            logger.error("Login Failed, credentials not provided.")
            raise HTTPUnauthorized
        if await self.logged_in:
            return
        data = {"email": self.email, "pword": self.password, "authenticate": "signin"}
        await self.session.post(
            "https://www.saltybet.com/authenticate?signin=1", data=data
        )
        if not await self.logged_in:
            logger.error("Login Failed, check your credentials.")
            raise HTTPUnauthorized
        self._last_login = pendulum.now()

    async def _get_raw_zdata_json(self) -> Optional[dict]:
        jresp: dict = {}
        async with self.session.get("https://www.saltybet.com/zdata.json") as resp:
            html = await resp.read()
            if html != "":
                jresp = await resp.json(content_type="text/html")
            else:
                return None
        return jresp

    async def _get_raw_ajax_get_stats_php(self) -> Optional[dict]:
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Match stats only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Match stats only available with illuminati membership.")
            return None

        jresp: dict = {}
        async with self.session.get(
            "https://www.saltybet.com/ajax_get_stats.php"
        ) as resp:
            jresp = await resp.json(content_type="text/html")
        return jresp

    async def _get_raw_state_json(self) -> dict:
        state = {}
        async with self.session.get("https://www.saltybet.com/state.json") as resp:
            state = await resp.json(content_type="text/html")
        return state

    # State Parsing
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

    def _alert_to_GameMode(self, alert: str) -> GameMode:
        game_mode: GameMode = GameMode.UNKNOWN
        if alert == "Tournament mode start!":
            game_mode = GameMode.TOURNAMENT
        elif alert == "Exhibition mode start!":
            game_mode = GameMode.EXHIBITION
        else:
            logger.warn(f"Unable to parse game mode from: {alert}")
        return game_mode

    def _remaining_to_GameMode(self, remaining: str) -> GameMode:
        game_mode: GameMode = GameMode.UNKNOWN
        if remaining.endswith("in the bracket!"):
            game_mode = GameMode.TOURNAMENT
        elif remaining.endswith("exhibition matches left!"):
            game_mode = GameMode.EXHIBITION
        elif remaining.endswith("next tournament!"):
            game_mode = GameMode.MATCHMAKING
        else:
            logger.warn(f"Unable to parse game mode from: {remaining}")
        return game_mode

    def _parse_remaining_rounds(self, remaining: str) -> Optional[int]:
        until_next: Optional[int] = None
        for known_line in [
            "exhibition matches left!",
            "in the bracket!",
            "next tournament!",
        ]:
            if remaining.endswith(known_line):
                until_next = int(remaining.split(" ")[0])
        if until_next is None:
            logger.warn(f"Unable to parse remaining rounds from: {remaining}")
        return until_next

    # Public Actions
    async def login(self, email: str, password: str):
        self.email = email
        self.password = password
        await self._login()

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
        async with self.session.post(
            "https://www.saltybet.com/ajax_place_bet.php", data=data
        ) as resp:
            text = await resp.text()
            if text == "":
                logger.error("Failed to place bet.")
            else:
                logger.debug("Bet placed successfully")

    async def get_bettors(self) -> Optional[Bettors]:
        """Fetches data from zdata.json"""
        jresp = await self._get_raw_zdata_json()
        if jresp is None:
            return None
        bettors: Bettors = {
            "match": {"red_fighters": [], "blue_fighters": []},
            "bettors": [],
        }
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
            if k in [
                "p1name",
                "p2name",
                "p1total",
                "p2total",
                "status",
                "alert",
                "x",
                "remaining",
            ]:
                continue
            elif "n" not in v or "b" not in v:
                continue
            bettor: Bettor = {
                "bettor_id": int(k),
                "username": v["n"],
                "balance": int(v["b"]),
            }
            if "p" in v:
                bettor["bet_side"] = SideColor(int(v["p"]))
            if "w" in v:
                bettor["wager"] = int(v["w"])
            if "r" in v:
                if len(v["r"]) > 3:
                    bettor["avatar"] = f"https://www.gravatar.com/avatar/{v['r']}"
                else:
                    bettor[
                        "avatar"
                    ] = f"https://www.saltybet.com/images/ranksmall/rank{v['r']}.png"
            if "g" in v:
                bettor["illuminati"] = v["g"] == "1"
            if "c" in v and v["c"] != "0" and "," in v["c"]:
                bettor["color_r"], bettor["color_g"], bettor["color_b"] = v["c"].split(
                    ","
                )
            bettors["bettors"].append(bettor)
        return bettors

    async def get_match_stats(self) -> Optional[Match]:
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
            red_1["tier"], red_2["tier"] = [
                Tier[tier] for tier in jresp["p1tier"].split(" / ")
            ]
            red_1["life"], red_2["life"] = jresp["p1life"].split(" / ")
            red_1["meter"], red_2["meter"] = jresp["p1meter"].split(" / ")
            red_1["palette"], red_2["palette"] = jresp["p1palette"].split(" / ")
            red_1["total_matches"], red_2["total_matches"] = [
                int(tm) for tm in jresp["p1totalmatches"].split(" / ")
            ]
            red_1["win_rate"], red_2["win_rate"] = [
                (Decimal(wr) / Decimal(100)) for wr in jresp["p1winrate"].split(" / ")
            ]
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
            blue_1["tier"], blue_2["tier"] = [
                Tier[tier] for tier in jresp["p2tier"].split(" / ")
            ]
            blue_1["life"], blue_2["life"] = jresp["p2life"].split(" / ")
            blue_1["meter"], blue_2["meter"] = jresp["p2meter"].split(" / ")
            blue_1["palette"], blue_2["palette"] = jresp["p2palette"].split(" / ")
            blue_1["total_matches"], blue_2["total_matches"] = [
                int(tm) for tm in jresp["p2totalmatches"].split(" / ")
            ]
            blue_1["win_rate"], blue_2["win_rate"] = [
                (Decimal(wr) / Decimal(100)) for wr in jresp["p2winrate"].split(" / ")
            ]
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

    # State Parsing
    async def get_state(self) -> Match:
        state = await self._get_raw_state_json()

        match: Match = {}

        # MatchStatus
        match["status"] = self._status_to_MatchStatus(state["status"])

        # GameMode
        mode: GameMode = self._alert_to_GameMode(state["alert"])
        if mode == GameMode.UNKNOWN:
            mode = self._remaining_to_GameMode(state["remaining"])
        match["mode"] = mode

        # Matches Remaining
        matches_left = self._parse_remaining_rounds(state["remaining"])
        if matches_left:
            self._matches_left = matches_left

        # Red Team
        p1name = state["p1name"]
        if not p1name.startswith("Team "):
            match["red_fighters"] = [{"name": p1name}]
        match["red_team_name"] = p1name

        # Blue Team
        p2name = state["p2name"]
        if not p2name.startswith("Team "):
            match["blue_fighters"] = [{"name": p2name}]
        match["blue_team_name"] = p2name

        # Bets
        match["red_bets"] = int(state["p1total"].replace(",", ""))
        match["blue_bets"] = int(state["p2total"].replace(",", ""))

        self._match = match

        return match
