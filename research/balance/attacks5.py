"""'Deliver only after the counterpart delivers' on both sides: does it erase the reneger's gain, and at what cost?"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from attacks import gain, one, every, show
from barter_sim import Smart, SmartReneger

class Strict(Smart):
    def delivers(self, deal, rnd):
        side = 'b' if deal['a'] == self.me else 'a'
        return deal['paid_' + side] and (deal['b'] if deal['a'] == self.me else deal['a']) not in self.cheated_by

show('SmartReneger among Strict (both sides wait)', gain(one(SmartReneger, Strict), every(Strict), False))
show('all Strict vs all Smart (handshake)', gain(every(Strict), every(Smart), False))
