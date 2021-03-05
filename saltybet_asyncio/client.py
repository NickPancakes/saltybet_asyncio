#!/usr/bin/env python3

import logging

from .websocket import WebsocketClient
from .scrape import ScraperClient


logger = logging.getLogger(__name__)


class SaltybetClient(WebsocketClient, ScraperClient):
    def __init__(self):
        WebsocketClient.__init__(self)
        ScraperClient.__init__(self)

    async def init(self):
        await WebsocketClient.init(self)
        await ScraperClient.init(self)

    async def shutdown(self):
        await WebsocketClient.shutdown(self)
