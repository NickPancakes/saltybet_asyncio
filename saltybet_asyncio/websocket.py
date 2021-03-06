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
    Match,
)

logger = logging.getLogger(__name__)


class WebsocketClient(BasicClient):
    def __init__(self):
        # State
        self.running: bool = False
        self._last_match_status: MatchStatus = MatchStatus.UNKNOWN
        self._last_game_mode: GameMode = GameMode.UNKNOWN

        # Connections
        self.sio: AsyncClient = None

        # Triggers
        # Start / End
        self._on_start_triggers: List[Callable[[], Awaitable[None]]] = []
        self._on_end_triggers: List[Callable[[], Awaitable[None]]] = []
        # MatchStatus Change
        self._on_status_change_triggers: List[Callable[[Match], Awaitable[None]]] = []
        self._on_status_open_triggers: List[Callable[[Match], Awaitable[None]]] = []
        self._on_status_locked_triggers: List[Callable[[Match], Awaitable[None]]] = []
        self._on_status_complete_triggers: List[Callable[[Match], Awaitable[None]]] = []
        # GameMode Change
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

        # Fetch state.json
        match: Match = await self.get_state()

        # Add in Illuminati Stats (ajax_get_stats.php) if available
        if await self.illuminati:
            illum_match_stats = await self.get_match_stats()
            if illum_match_stats:
                match.update(illum_match_stats)
                self._match = match

        await self._trigger_events(match)

    # Event Triggers
    async def _trigger_events(self, match: Match):
        """Fires registered event triggers based on State"""

        # Fire Triggers
        match_status: MatchStatus = match["status"]
        if match_status != self._last_match_status:
            logger.debug(
                f"Match status changed from {self._last_match_status} to {match_status.name}"
            )
            self._last_match_status = match_status
            await self._trigger_status_change(match)

        game_mode: GameMode = match["mode"]
        if game_mode != self._last_game_mode:
            logger.debug(
                f"Game mode changed from {self._last_game_mode.name} to {game_mode.name}"
            )
            self._last_game_mode = game_mode
            await self._trigger_mode_change(game_mode)

    async def _trigger_status_change(self, match: Match):
        trigger_funcs: List[Callable[[Match], Awaitable[None]]] = []
        trigger_funcs.extend(self._on_status_change_triggers)
        if match["status"] == MatchStatus.OPEN:
            trigger_funcs.extend(self._on_status_open_triggers)
        elif match["status"] == MatchStatus.LOCKED:
            trigger_funcs.extend(self._on_status_locked_triggers)
        elif match["status"] in [
            MatchStatus.RED_WINS,
            MatchStatus.BLUE_WINS,
            MatchStatus.DRAW,
        ]:
            trigger_funcs.extend(self._on_status_complete_triggers)
        # Execute all async
        await asyncio.gather(*[f(match) for f in trigger_funcs])

    async def _trigger_mode_change(self, game_mode: GameMode):
        trigger_funcs: List[Callable[[GameMode], Awaitable[None]]] = []
        trigger_funcs.extend(self._on_mode_change_triggers)
        if game_mode == GameMode.TOURNAMENT:
            trigger_funcs.extend(self._on_mode_tournament_triggers)
        elif game_mode == GameMode.EXHIBITION:
            trigger_funcs.extend(self._on_mode_exhibition_triggers)
        elif game_mode == GameMode.MATCHMAKING:
            trigger_funcs.extend(self._on_mode_matchmaking_triggers)
        # Execute all async
        await asyncio.gather(*[f(game_mode) for f in trigger_funcs])

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

    def on_status_change(
        self, func: Callable[[Match], Awaitable[None]]
    ) -> Callable[[Match], Awaitable[None]]:
        if func not in self._on_status_change_triggers:
            self._on_status_change_triggers.append(func)
        return func

    def on_status_open(
        self, func: Callable[[Match], Awaitable[None]]
    ) -> Callable[[Match], Awaitable[None]]:
        if func not in self._on_status_open_triggers:
            self._on_status_open_triggers.append(func)
        return func

    def on_status_locked(
        self, func: Callable[[Match], Awaitable[None]]
    ) -> Callable[[Match], Awaitable[None]]:
        if func not in self._on_status_locked_triggers:
            self._on_status_locked_triggers.append(func)
        return func

    def on_status_complete(
        self, func: Callable[[Match], Awaitable[None]]
    ) -> Callable[[Match], Awaitable[None]]:
        if func not in self._on_status_complete_triggers:
            self._on_status_complete_triggers.append(func)
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
