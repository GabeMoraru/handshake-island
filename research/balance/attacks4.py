"""Full report for the proposed fix: variant A recipes (rope 2 each) plus 12 starting fish (no model calls)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import barter_sim as bs
import attacks2
bs.START_FISH = 12
bs.RECIPES = attacks2.V1
attacks2.report('v1 + 12 fish')
bs.RECIPES = attacks2.VARIANT_A
attacks2.report('variant A + 12 fish')
