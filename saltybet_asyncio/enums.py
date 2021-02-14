#!/usr/bin/env python3
from enum import Enum


class BettingStatus(Enum):
    UNKNOWN = 0
    OPEN = 1
    LOCKED = 2
    RED_WINS = 3
    BLUE_WINS = 4


class GameMode(Enum):
    UNKNOWN = 0
    MATCHMAKING = 1
    TOURNAMENT = 2
    EXHIBITION = 3


class BettingSide(Enum):
    UNKNOWN = 0
    RED = 1
    BLUE = 2


class Tier(Enum):
    UNKNOWN = 0
    S = 1
    A = 2
    B = 3
    P = 4
    X = 5


class Upgrade(Enum):
    UNKNOWN = 0
    UNLOCK = 1
    PROMOTE = 2
    LIFE_INCREASE = 3
    LIFE_DECREASE = 4
    METER_INCREASE = 5
    METER_DECREASE = 6
