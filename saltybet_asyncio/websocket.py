#!/usr/bin/env python3

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import List

from socketio import AsyncClient

from .base import BasicClient
from .types import (
    GameMode,
    MatchStatus,
)

logger = logging.getLogger(__name__)


class WebsocketClient(BasicClient):
    def __init__(self):
        # State
        self.running: bool = False

        # Connections
        self.sio: AsyncClient = None

        # Triggers
        self._on_start_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_end_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_betting_change_triggers: List[
            Callable[[MatchStatus, str, int, str, int], Awaitable[None]]
        ] = []
        self._on_betting_open_triggers: List[
            Callable[[MatchStatus, str, int, str, int], Awaitable[None]]
        ] = []
        self._on_betting_locked_triggers: List[
            Callable[[MatchStatus, str, int, str, int], Awaitable[None]]
        ] = []
        self._on_betting_payout_triggers: List[
            Callable[[MatchStatus, str, int, str, int], Awaitable[None]]
        ] = []
        self._on_mode_change_triggers: List[Callable[[GameMode], Awaitable[None]]] = []
        self._on_mode_tournament_triggers: List[
            Callable[[GameMode], Awaitable[None]]
        ] = []
        self._on_mode_exhibition_triggers: List[
            Callable[[GameMode], Awaitable[None]]
        ] = []
        self._on_mode_matchmaking_triggers: List[
            Callable[[GameMode], Awaitable[None]]
        ] = []

        # Parent __init__()
        super().__init__()

    async def init(self):
        if not self.initialized:
            if self.sio is None:
                # SocketIO Client
                self.sio = AsyncClient()
                # Register Websocket Handler
                self.sio.on("message", self._on_message)

            # On_Start Event
            for f in self._on_start_triggers:
                await f()

        # Parent init()
        await super().init()

    async def run(self):
        await self.init()
        self.running = True

        # Listen for Events
        while True:
            try:
                await self.sio.connect("https://www.saltybet.com:2096")
                await self.sio.wait()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.info(f"Encountered error: {e}\nAttempting to restart.")

        await self.shutdown()

    async def shutdown(self):
        if self.running:
            # On_End Event
            for f in self._on_end_triggers:
                await f()
            logger.info("Closing connections.")
            await self.sio.disconnect()
            await self.session.close()
            self.running = False

        # Parent shutdown()
        await super().shutdown()

    # SocketIO Connection / Event Handling
    async def _on_message(self):
        """Parses state.json when indicated to do so by websocket"""
        logger.debug("Socket.io Message Received")
        state = await self._get_state()

        # Fire Triggers
        match_status = state["status"]
        if match_status != self._last_match_status:
            self._last_match_status = match_status
            logger.debug(f"Current status changed to {match_status.name}")
            await self._trigger_betting_change(match_status)

        game_mode = state["mode"]
        if game_mode != self._last_game_mode:
            self._last_game_mode = game_mode
            logger.debug(f"Current mode changed to {game_mode.name}")
            await self._trigger_mode_change(game_mode)

    async def _trigger_betting_change(self, match_status: MatchStatus):
        await asyncio.gather(
            *[
                f(
                    match_status,
                    self._match["red_team_name"],
                    self._match["red_bets"],
                    self._match["blue_team_name"],
                    self._match["blue_bets"],
                )
                for f in self._on_betting_change_triggers
            ]
        )
        if match_status == MatchStatus.OPEN:
            await asyncio.gather(
                *[
                    f(
                        match_status,
                        self._match["red_team_name"],
                        0,
                        self._match["blue_team_name"],
                        0,
                    )
                    for f in self._on_betting_open_triggers
                ]
            )
        elif match_status == MatchStatus.LOCKED:
            await asyncio.gather(
                *[
                    f(
                        match_status,
                        self._match["red_team_name"],
                        self._match["red_bets"],
                        self._match["blue_team_name"],
                        self._match["blue_bets"],
                    )
                    for f in self._on_betting_locked_triggers
                ]
            )
        elif match_status in [
            MatchStatus.RED_WINS,
            MatchStatus.BLUE_WINS,
            MatchStatus.DRAW,
        ]:
            await asyncio.gather(
                *[
                    f(
                        match_status,
                        self._match["red_team_name"],
                        self._match["red_bets"],
                        self._match["blue_team_name"],
                        self._match["blue_bets"],
                    )
                    for f in self._on_betting_payout_triggers
                ]
            )

    async def _trigger_mode_change(self, game_mode: GameMode):
        await asyncio.gather(*[f(game_mode) for f in self._on_mode_change_triggers])
        if game_mode == GameMode.TOURNAMENT:
            await asyncio.gather(
                *[f(game_mode) for f in self._on_mode_tournament_triggers]
            )
        elif game_mode == GameMode.EXHIBITION:
            await asyncio.gather(
                *[f(game_mode) for f in self._on_mode_exhibition_triggers]
            )
        elif game_mode == GameMode.MATCHMAKING:
            await asyncio.gather(
                *[f(game_mode) for f in self._on_mode_matchmaking_triggers]
            )

    # Event Decorators
    def on_start(
        self, func: Callable[[], Awaitable[None]]
    ) -> Callable[[], Awaitable[None]]:
        if func not in self._on_start_triggers:
            self._on_start_triggers.append(func)
        return func

    def on_end(
        self, func: Callable[[], Awaitable[None]]
    ) -> Callable[[], Awaitable[None]]:
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

    def on_mode_change(
        self, func: Callable[[GameMode], Awaitable[None]]
    ) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_change_triggers:
            self._on_mode_change_triggers.append(func)
        return func

    def on_mode_tournament(
        self, func: Callable[[GameMode], Awaitable[None]]
    ) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_tournament_triggers:
            self._on_mode_tournament_triggers.append(func)
        return func

    def on_mode_exhibition(
        self, func: Callable[[GameMode], Awaitable[None]]
    ) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_exhibition_triggers:
            self._on_mode_exhibition_triggers.append(func)
        return func

    def on_mode_matchmaking(
        self, func: Callable[[GameMode], Awaitable[None]]
    ) -> Callable[[GameMode], Awaitable[None]]:
        if func not in self._on_mode_matchmaking_triggers:
            self._on_mode_matchmaking_triggers.append(func)
        return func
