#!/usr/bin/env python3

from decimal import Decimal
from enum import IntEnum
from typing import Dict, TypedDict, List


class BettingStatus(IntEnum):
    UNKNOWN = 0
    OPEN = 1
    LOCKED = 2
    RED_WINS = 3
    BLUE_WINS = 4


class GameMode(IntEnum):
    UNKNOWN = 0
    MATCHMAKING = 1
    TOURNAMENT = 2
    EXHIBITION = 3


class BettingSide(IntEnum):
    UNKNOWN = 0
    RED = 1
    BLUE = 2


class Tier(IntEnum):
    UNKNOWN = 0
    S = 1
    A = 2
    B = 3
    P = 4
    X = 5


class UpgradeType(IntEnum):
    UNKNOWN = 0
    UNLOCK = 1
    PROMOTE = 2
    LIFE_INCREASE = 3
    LIFE_DECREASE = 4
    METER_INCREASE = 5
    METER_DECREASE = 6


class Upgrade(TypedDict, total=False):  # pylint: disable=inherit-non-class
    upgrade_type: UpgradeType
    username: str
    value: int


class Fighter(TypedDict, total=False):  # pylint: disable=inherit-non-class
    fighter_id: int
    name: str
    tier: Tier
    author: str
    life: int
    meter: int
    palette: int
    sprite: str
    win_rate: Decimal
    total_matches: int
    upgrades: List[Upgrade]


class Tournament(TypedDict, total=False):  # pylint: disable=inherit-non-class
    tournament_id: int
    name: str
    mode: GameMode
    match_ids: List[int]


class Match(TypedDict, total=False):  # pylint: disable=inherit-non-class
    match_id: int
    tournament: Tournament
    status: BettingStatus
    mode: GameMode
    red_fighter: Fighter
    red_bets: int
    blue_fighter: Fighter
    blue_bets: int


class Bettor(TypedDict, total=False):  # pylint: disable=inherit-non-class
    bettor_id: int
    username: str
    balance: int
    wager: int
    bet_side: BettingSide
    illuminati: bool
    avatar: str
    color_r: str
    color_g: str
    color_b: str


class Bettors(TypedDict, total=False):  # pylint: disable=inherit-non-class
    match: Match
    bettors: List[Bettor]

