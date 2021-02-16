#!/usr/bin/env python3

from enum import IntEnum
from typing import TypedDict, List


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
    _type: UpgradeType
    username: str
    value: int


class Fighter(TypedDict, total=False):  # pylint: disable=inherit-non-class
    _id: int
    name: str
    tier: Tier
    author: str
    life: int
    meter: int
    sprite: str
    upgrades: List[Upgrade]


class Tournament(TypedDict, total=False):  # pylint: disable=inherit-non-class
    _id: int
    name: str
    mode: GameMode
    match_ids: List[int]


class Match(TypedDict, total=False):  # pylint: disable=inherit-non-class
    _id: int
    tournament_id: int
    mode: GameMode
    status: BettingStatus
    red_fighter_id: str
    red_fighter_name: str
    red_tier: Tier
    red_life: int
    red_meter: int
    red_pallette: int
    red_bets: int
    blue_fighter_id: str
    blue_fighter_name: str
    blue_tier: Tier
    blue_life: int
    blue_meter: int
    blue_pallette: int
    blue_bets: int
    winner: BettingSide


class MatchStats(TypedDict, total=False):  # pylint: disable=inherit-non-class
    p1name: str
    p1totalmatches: str
    p1winrate: str
    p1tier: str
    p1life: str
    p1meter: str
    p1author: str
    p1palette: str
    p2name: str
    p2totalmatches: str
    p2winrate: str
    p2tier: str
    p2life: str
    p2meter: str
    p2author: str
    p2palette: str


class State(TypedDict, total=False):  # pylint: disable=inherit-non-class
    red_fighter: str
    blue_fighter: str
    red_bets: int
    blue_bets: int
    game_mode: GameMode
    betting_status: BettingStatus
