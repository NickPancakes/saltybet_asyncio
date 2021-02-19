#!/usr/bin/env python3
"""pylint option block-disable"""

import asyncio
from collections.abc import Callable, Awaitable
from decimal import Decimal
import logging
from typing import List, Tuple, Optional

import aiohttp
import aiorun
import backoff
import pendulum
import socketio
from aiohttp.web import HTTPUnauthorized
from selectolax.parser import HTMLParser  # pylint: disable=no-name-in-module

from .types import Fighter, Match, Tournament, Upgrade, BettingSide, BettingStatus, GameMode, Tier, UpgradeType, Bettors, Bettor


logger = logging.getLogger(__name__)


class SaltybetClient:
    def __init__(self):
        # pylint: disable=unsubscriptable-object
        # Connections
        self.session: aiohttp.ClientSession = None
        self.sio: socketio.AsyncClient = None
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)

        # State
        self._betting_status: BettingStatus = BettingStatus.UNKNOWN
        self._game_mode: GameMode = GameMode.UNKNOWN
        self._tournament_id: int = 0
        self._match_id: int = 0
        self._red_fighter_name: str = ""
        self._red_bets: int = 0
        self._blue_fighter_name: str = ""
        self._blue_bets: int = 0

        # Credentials
        self.email: Optional[str] = None
        self.password: Optional[str] = None

        # Triggers
        self._on_start_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_end_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_betting_change_triggers: List[Callable[[BettingStatus, str, int, str, int], Awaitable[None]]] = []
        self._on_betting_open_triggers: List[Callable[[str, str], Awaitable[None]]] = []
        self._on_betting_locked_triggers: List[Callable[[[str, int, str, int], Awaitable[None]]]] = []
        self._on_betting_payout_triggers: List[Callable[[str, int, str, int], Awaitable[None]]] = []
        self._on_mode_change_triggers: List[Callable[[GameMode], Awaitable[None]]] = []
        self._on_mode_tournament_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_mode_exhibition_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_mode_matchmaking_triggers: List[Callable[[], Awaitable[None]]] = []

    async def _init(self):
        if self.session is None:
            # Create aiohttp session
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10, limit_per_host=5))
        if self.sio is None:
            # SocketIO Client
            self.sio = socketio.AsyncClient()

    # Async Properties
    @property
    async def logged_in(self) -> bool:
        if self.email is None or self.password is None:
            return False
        logged_in = True
        async with self.session.get("https://www.saltybet.com/") as resp:
            if not resp.ok:
                logging.error(f"Response code {resp.status} from {resp.url}.")
                return False
            html = await resp.read()
            selector = ".nav-text > a:nth-child(1) > span:nth-child(1)"
            for node in HTMLParser(html).css(selector):
                if "Sign in" in node.text():
                    logged_in = False
                    break
        return logged_in

    @property
    async def illuminati(self) -> bool:
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Illuminati status cannot be checked without being logged in.")
            return False
        illuminati = False
        async with self.session.get("https://www.saltybet.com/") as resp:
            if not resp.ok:
                logging.error(f"Response code {resp.status} from {resp.url}.")
                return False
            html = await resp.read()
            selector = ".navbar-text > span:nth-child(1)"
            for node in HTMLParser(html).css(selector):
                if "goldtext" in node.attributes["class"]:
                    illuminati = True
                    break
        return illuminati

    @property
    async def balance(self) -> int:
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Balance only available when logged in.")
            return 0
        balance = 0
        async with self.session.get("https://www.saltybet.com/") as resp:
            if not resp.ok:
                logging.error(f"Response code {resp.status} from {resp.url}.")
                return 0
            html = await resp.read()
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
        if self._tournament_id is not None:
            return self._tournament_id
        async with self.session.get("https://www.saltybet.com/stats?tournamentstats=1&page=1") as resp:
            if not resp.ok:
                logging.error(f"Response code {resp.status} from {resp.url}.")
                return None
            html = await resp.read()
            top_result_node = HTMLParser(html).css_first(
                ".leaderboard > tbody:nth-child(2) > tr:nth-child(1) > td:nth-child(1) > a:nth-child(1)"
            )
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

        if self._match_id is not None:
            return self._match_id

        tournament_id = await self.tournament_id
        async with self.session.get(f"https://www.saltybet.com/stats?tournament_id={tournament_id}") as resp:
            html = await resp.read()
            tree = HTMLParser(html)
            top_row = tree.css_first(".leaderboard > tbody:nth-child(2) > tr:nth-child(1) > td:nth-child(1) > a:nth-child(1)")
            match_link = top_row.attrs["href"]
            self._match_id = match_link.split("=")[1]
        return self._match_id

    # Properties parsed from state.json
    @property
    async def betting_status(self) -> BettingStatus:
        if self._betting_status is None:
            await self._get_state(store=True)
        return self._betting_status

    @property
    async def game_mode(self) -> GameMode:
        if self._game_mode is None:
            await self._get_state(store=True)
        return self._game_mode

    @property
    async def red_fighter(self) -> Optional[str]:  # pylint: disable=unsubscriptable-object
        if self._red_fighter_name is None:
            await self._get_state(store=True)
        return self._red_fighter_name

    @property
    async def blue_fighter(self) -> Optional[str]:  # pylint: disable=unsubscriptable-object
        if self._blue_fighter_name is None:
            await self._get_state(store=True)
        return self._blue_fighter_name

    @property
    async def red_bets(self) -> Optional[int]:  # pylint: disable=unsubscriptable-object
        if self._red_bets is None:
            await self._get_state(store=True)
        return self._red_bets

    @property
    async def blue_bets(self) -> Optional[int]:  # pylint: disable=unsubscriptable-object
        if self._blue_bets is None:
            await self._get_state(store=True)
        return self._blue_bets

    # Actions
    async def login(self, email: str, password: str):
        self.email = email
        self.password = password
        await self._init()
        await self._login()

    async def _login(self):
        logged_in = await self.logged_in
        if logged_in:
            return
        if self.email is None or self.password is None:
            logger.error("Login Failed, credentials not provided.")
            raise HTTPUnauthorized
        data = {"email": self.email, "pword": self.password, "authenticate": "signin"}
        await self.session.post("https://www.saltybet.com/authenticate?signin=1", data=data)
        logged_in = await self.logged_in
        if not logged_in:
            logger.error("Login Failed, check your credentials.")
            raise HTTPUnauthorized

    async def place_bet(self, side: BettingSide, wager: int):
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
        if side == BettingSide.RED:
            player = "player1"
        elif side == BettingSide.BLUE:
            player = "player2"
        data = {"selectedplayer": player, "wager": wager}
        async with self.session.post("https://www.saltybet.com/ajax_place_bet.php", data=data) as resp:
            text = await resp.text()
            if text == "":
                logger.error("Failed to place bet.")
            else:
                logger.debug("Bet placed successfully")

    async def get_bettors(self) -> Optional[Bettors]:  # pylint: disable=unsubscriptable-object
        """Fetches data from zdata.json"""
        bettors: Bettors = {"match": {"red_fighter": {}, "blue_fighter": {}}, "bettors": []}
        async with self.session.get("https://www.saltybet.com/zdata.json") as resp:
            html = await resp.read()
            if html != "":
                jresp = await resp.json(content_type="text/html")
                bettors["match"]["status"] = self._status_to_BettingStatus(jresp["status"])
                bettors["match"]["red_fighter"]["name"] = jresp["p1name"]
                bettors["match"]["blue_fighter"]["name"] = jresp["p1name"]
                bettors["match"]["red_bets"] = int(jresp["p1total"].replace(",", ""))
                bettors["match"]["blue_bets"] = int(jresp["p2total"].replace(",", ""))
                for k, v in jresp.items():
                    if k in ["p1name", "p2name", "p1total", "p2total", "status", "alert", "x", "remaining"]:
                        continue
                    bettor: Bettor = {"bettor_id": int(k), "username": v["n"], "balance": int(v["b"])}
                    if "p" in v:
                        bettor["bet_side"] = BettingSide(int(v["p"]))
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

    async def get_match_stats(self) -> Optional[Match]:  # pylint: disable=unsubscriptable-object
        """Fetches data from ajax_get_stats.php"""
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Match stats only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Match stats only available with illuminati membership.")
            return None
        stats: Match = {}
        async with self.session.get("https://www.saltybet.com/ajax_get_stats.php") as resp:
            html = await resp.read()
            if html != "":
                jresp = await resp.json(content_type="text/html")
                red_fighter: Fighter = {
                    "name": jresp["p1name"],
                    "author": jresp["p1author"],
                    "tier": Tier[jresp["p1tier"]],
                    "life": jresp["p1life"],
                    "meter": jresp["p1meter"],
                    "palette": jresp["p1palette"],
                    "total_matches": int(jresp["p1totalmatches"]),
                    "win_rate": Decimal(jresp["p1winrate"]) / Decimal(100),
                }
                stats["red_fighter"] = red_fighter
                blue_fighter: Fighter = {
                    "name": jresp["p2name"],
                    "author": jresp["p2author"],
                    "tier": Tier[jresp["p2tier"]],
                    "life": jresp["p2life"],
                    "meter": jresp["p2meter"],
                    "palette": jresp["p2palette"],
                    "total_matches": int(jresp["p2totalmatches"]),
                    "win_rate": Decimal(jresp["p2winrate"]) / Decimal(100),
                }
                stats["blue_fighter"] = blue_fighter
        return stats

    # Scraper Functions
    def _split_tournament_name_and_mode(self, tournament_name: str) -> Tuple[GameMode, str]:
        EXHIBITIONS_TAG = "(Exhibitions)"
        MATCHMAKING_TAG = "(Matchmaking)"
        tournament_title = None
        tournament_mode = None
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

    @backoff.on_predicate(backoff.expo, lambda x: x is None, max_tries=10)
    async def scrape_tournament(self, tournament_id: int) -> Optional[Tournament]:  # pylint: disable=unsubscriptable-object
        try:
            await self._login()
        except HTTPUnauthorized:
            logger.error("Tournament scraping only available when logged in.")
            return None
        if not await self.illuminati:
            logger.error("Tournament scraping only available with illuminati membership.")
            return None

        tournament: Tournament = {"tournament_id": tournament_id, "mode": GameMode.UNKNOWN, "match_ids": []}
        async with self._semaphore:
            async with self.session.get(f"https://www.saltybet.com/stats?tournament_id={tournament_id}") as resp:
                html = await resp.read()
                tree = HTMLParser(html)
                rows = tree.css(".leaderboard > tbody:nth-child(2) > tr")
                if not rows:
                    return None

                # Name and Mode
                tournament_name = tree.css_first("#result > strong:nth-child(1)").text()
                tournament["mode"], tournament["name"] = self._split_tournament_name_and_mode(tournament_name)

                # Match IDs
                for row in rows:
                    match_link = row.css_first("td:nth-child(1) > a:nth-child(1)").attrs["href"]
                    match_id = match_link.split("=")[1]
                    tournament["match_ids"].append(match_id)
        return tournament

    @backoff.on_predicate(backoff.expo, lambda x: x is None, max_tries=10)
    async def scrape_match(self, tournament_id: int, match_id: int) -> Optional[Match]:  # pylint: disable=unsubscriptable-object
        match: Match = {
            "match_id": match_id,
            "mode": GameMode.UNKNOWN,
            "status": BettingStatus.UNKNOWN,
            "tournament": {"tournament_id": tournament_id,},
            "red_fighter": {},
            "blue_fighter": {},
            "red_bets": 0,
            "blue_bets": 0,
        }
        async with self._semaphore:
            try:
                await self._login()
            except HTTPUnauthorized:
                logger.error("Match scraping only available when logged in.")
                return None
            if not await self.illuminati:
                logger.error("Match scraping only available with illuminati membership.")
                return None

            async with self.session.get(f"https://www.saltybet.com/stats?match_id={match_id}") as resp:
                html = await resp.read()
                tree = HTMLParser(html)
                # Determine if Emptry
                rows = tree.css(".leaderboard > tbody:nth-child(2) > tr")
                if not rows:
                    return None
                result_node = tree.css_first("#result")
                # Winner
                winner_class = result_node.css_first("span").attrs["class"]
                if "redtext" in winner_class:
                    match["status"] = BettingStatus.RED_WINS
                elif "bluetext" in winner_class:
                    match["status"] = BettingStatus.BLUE_WINS
                # Title / Fighters
                title = result_node.text(deep=False).strip().replace("Winner:", "")
                match["red_fighter"]["name"], remaining_title = title.split(" vs ")
                match["blue_fighter"]["name"], remaining_title = remaining_title.split(" at ")
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

    @backoff.on_predicate(backoff.expo, lambda x: x is None, max_tries=10)
    async def scrape_compendium(self, tier: Tier) -> Optional[List[Fighter]]:  # pylint: disable=unsubscriptable-object
        fighters: List[Fighter] = []
        async with self._semaphore:
            try:
                await self._login()
            except HTTPUnauthorized:
                logger.error("Compendium scraping only available when logged in.")
                return None
            if not await self.illuminati:
                logger.error("Compendium scraping only available with illuminati membership.")
                return None

            async with self.session.get(f"https://www.saltybet.com/compendium?tier={tier.value}") as resp:
                html = await resp.read()
                tree = HTMLParser(html)
                rows = tree.css("#tierlist > li")
                if not rows:
                    return None
                for row in rows:
                    fighter_id = row.css_first("a:nth-child(1)").attrs["href"].split("=")[-1]
                    fighters.append(
                        {"name": row.text(), "fighter_id": fighter_id, "tier": tier,}
                    )
        return fighters

    @backoff.on_predicate(backoff.expo, lambda x: x is None, max_tries=10)
    async def scrape_fighter(self, tier: Tier, fighter_id: int) -> Optional[Fighter]:  # pylint: disable=unsubscriptable-object
        fighter: Fighter = {}
        async with self._semaphore:
            try:
                await self._login()
            except HTTPUnauthorized:
                logger.error("Compendium scraping only available when logged in.")
                return None
            if not await self.illuminati:
                logger.error("Compendium scraping only available with illuminati membership.")
                return None

            async with self.session.get(f"https://www.saltybet.com/compendium?tier={tier.value}&character={fighter_id}") as resp:
                html = await resp.read()
                tree = HTMLParser(html)
                fighter = {
                    "name": tree.css_first(".statname").text(deep=False).strip(),
                    "fighter_id": fighter_id,
                    "tier": tier,
                    "author": tree.css_first("#basicstats").text(deep=False).strip().replace("by ", ""),
                    "life": int(
                        tree.css_first("table.detailedstats:nth-child(5) > tbody:nth-child(2) > tr:nth-child(2) > td:nth-child(1)").text()
                    ),
                    "meter": int(
                        tree.css_first("table.detailedstats:nth-child(5) > tbody:nth-child(2) > tr:nth-child(2) > td:nth-child(2)").text()
                    ),
                    "sprite": f"https://www.saltybet.com/images/charanim/{fighter_id}.gif",
                    "upgrades": [],
                }

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

    def _status_to_BettingStatus(self, status: str) -> BettingStatus:
        # Determine BettingStatus
        out = BettingStatus.UNKNOWN
        if status == "open":
            out = BettingStatus.OPEN
        elif status == "locked":
            out = BettingStatus.LOCKED
        elif status == "1":
            out = BettingStatus.RED_WINS
        elif status == "2":
            out = BettingStatus.BLUE_WINS
        else:
            logger.debug(f"Unhandled status: {status}")
        return out

    # State Parsing
    async def _get_state(self, store=False) -> Match:
        state = {}
        async with self.session.get("https://www.saltybet.com/state.json") as resp:
            state = await resp.json(content_type="text/html")

        out: Match = {
            "red_fighter": {},
            "blue_fighter": {},
        }

        out["status"] = self._status_to_BettingStatus(state["status"])

        # Determine GameMode
        out["mode"] = GameMode.UNKNOWN
        if state["alert"] == "Tournament mode start!":
            out["mode"] = GameMode.TOURNAMENT
        elif state["remaining"].endswith("in the bracket!"):
            out["mode"] = GameMode.TOURNAMENT
        elif state["alert"] == "Exhibition mode start!":
            out["mode"] = GameMode.EXHIBITION
        elif state["remaining"].endswith("exhibition matches left!"):
            out["mode"] = GameMode.EXHIBITION
        elif state["remaining"].endswith("next tournament!"):
            out["mode"] = GameMode.MATCHMAKING
        else:
            logger.debug(f"Unhandled alert: {state['alert']}")

        out["red_fighter"]["name"] = state["p1name"]
        out["blue_fighter"]["name"] = state["p2name"]
        out["red_bets"] = int(state["p1total"].replace(",", ""))
        out["blue_bets"] = int(state["p2total"].replace(",", ""))

        if store:
            self._betting_status = out["status"]
            self._game_mode = out["mode"]
            self._red_fighter_name = out["red_fighter"]["name"]
            self._blue_fighter_name = out["blue_fighter"]["name"]
            self._red_bets = out["red_bets"]
            self._blue_bets = out["blue_bets"]

        return out

    # SocketIO Connection / Event Handling
    async def _on_message(self):
        """Parses state.json when indicated to do so by websocket"""
        logger.debug("Socket.io Message Received")
        state = await self._get_state()

        # Update only fighters/bets
        self._red_fighter_name = state["red_fighter"]["name"]
        self._blue_fighter_name = state["blue_fighter"]["name"]
        self._red_bets = state["red_bets"]
        self._blue_bets = state["blue_bets"]

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

    async def _trigger_betting_change(self, betting_status: BettingStatus):
        await asyncio.gather(
            *[
                f(betting_status, self._red_fighter_name, self._red_bets, self._blue_fighter_name, self._blue_bets,)
                for f in self._on_betting_change_triggers
            ]
        )
        if betting_status == BettingStatus.OPEN:
            await asyncio.gather(*[f(self._red_fighter_name, self._blue_fighter_name) for f in self._on_betting_open_triggers])
        elif betting_status == BettingStatus.LOCKED:
            await asyncio.gather(
                *[
                    f(self._red_fighter_name, self._red_bets, self._blue_fighter_name, self._blue_bets)
                    for f in self._on_betting_locked_triggers
                ]
            )
        elif betting_status == BettingStatus.RED_WINS:
            await asyncio.gather(
                *[
                    f(self._red_fighter_name, self._red_bets, self._blue_fighter_name, self._blue_bets)
                    for f in self._on_betting_payout_triggers
                ]
            )
        elif betting_status == BettingStatus.BLUE_WINS:
            await asyncio.gather(
                *[
                    f(self._blue_fighter_name, self._blue_bets, self._red_fighter_name, self._red_bets)
                    for f in self._on_betting_payout_triggers
                ]
            )

    async def _trigger_mode_change(self, game_mode: GameMode):
        await asyncio.gather(*[f(game_mode) for f in self._on_mode_change_triggers])
        if game_mode == GameMode.TOURNAMENT:
            await asyncio.gather(*[f() for f in self._on_mode_tournament_triggers])
        elif game_mode == GameMode.EXHIBITION:
            await asyncio.gather(*[f() for f in self._on_mode_exhibition_triggers])
        elif game_mode == GameMode.MATCHMAKING:
            await asyncio.gather(*[f() for f in self._on_mode_matchmaking_triggers])

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
        self, func: Callable[[BettingStatus, str, int, str, int], Awaitable[None]]
    ) -> Callable[[BettingStatus, str, int, str, int], Awaitable[None]]:
        if func not in self._on_betting_change_triggers:
            self._on_betting_change_triggers.append(func)
        return func

    def on_betting_open(self, func: Callable[[str, str], Awaitable[None]]) -> Callable[[str, str], Awaitable[None]]:
        if func not in self._on_betting_open_triggers:
            self._on_betting_open_triggers.append(func)
        return func

    def on_betting_locked(self, func: Callable[[str, int, str, int], Awaitable[None]]) -> Callable[[str, int, str, int], Awaitable[None]]:
        if func not in self._on_betting_locked_triggers:
            self._on_betting_locked_triggers.append(func)
        return func

    def on_betting_payout(self, func: Callable[[str, int, str, int], Awaitable[None]]) -> Callable[[str, int, str, int], Awaitable[None]]:
        if func not in self._on_betting_payout_triggers:
            self._on_betting_payout_triggers.append(func)
        return func

    def on_mode_change(self, func: Callable[[GameMode], Awaitable[None]]) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_change_triggers:
            self._on_mode_change_triggers.append(func)
        return func

    def on_mode_tournament(self, func: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
        if func not in self._on_mode_tournament_triggers:
            self._on_mode_tournament_triggers.append(func)
        return func

    def on_mode_exhibition(self, func: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
        if func not in self._on_mode_exhibition_triggers:
            self._on_mode_exhibition_triggers.append(func)
        return func

    def on_mode_matchmaking(self, func: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
        if func not in self._on_mode_matchmaking_triggers:
            self._on_mode_matchmaking_triggers.append(func)
        return func

    async def run_forever(self):
        await self._init()

        # Register Websocket Handler
        self.sio.on("message", self._on_message)
        # Websocket Connect
        await self.sio.connect("https://www.saltybet.com:2096")

        # On_Start Event
        for f in self._on_start_triggers:
            await f()

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
