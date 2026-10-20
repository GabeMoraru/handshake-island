"""Liquidity check: does 12 starting fish change the talk-value gap or the reneger gain? (no model calls)"""
import os, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import barter_sim as bs
from attacks import gain, one, every
from attacks2 import V1, VARIANT_A, revealed, eff, fold_cast
from barter_sim import Smart, SmartCautious, SmartReneger

for name, rec in (('v1', V1), ('variant A', VARIANT_A)):
    bs.RECIPES = rec
    for fish in (6, 12):
        bs.START_FISH = fish
        r = gain(one(SmartReneger, SmartCautious), every(SmartCautious), False)
        print(f'{name:9s} fish {fish:2d}: Smart binding {eff(every(Smart), True):.2f}; truthful values {eff(revealed(lambda s, a, v: v), True):.2f};'
              f' net-folds {eff(fold_cast(lambda g, a: g["projects"][a] == "net"), True):.2f}; never-deliver among SmartCautious {r["gain"]:+.2f} (se {r["se"]:.2f})')
