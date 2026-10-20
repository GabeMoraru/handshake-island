# Reference labels for Jev

`reference.jsonl` labels the 240 speeches that `scripts/jev_validate.py --sample` draws from E1: 120 at random, then 120 more worded like an accusation or a promise, which are rare at random (`ACCUSING` and `PROMISING` in `evals/speech.py`). It has one line per speech and labeller.

Claude Opus 5.5 labelled every speech twice, in two independent passes that saw neither each other's labels nor Jev's answers: `claude-opus-5-5 first pass`, 120 at a time, and `claude-opus-5-5 second pass`, 30 at a time. Where the passes differed, Claude Opus 5.5 read the speech and both answers, unnamed and in alternating order, and decided the label by the rules below: `claude-opus-5-5 adjudicated`. Jev is scored against the adjudicated labels, and the two passes give the agreement (Cohen's kappa).

Each labeller read only the speech texts and the questions with their options (`evals/speech.py`), with this context and these rules:

> Six residents (Mara, Rowan, Marcus, Theo, Nia, Felix) trade wood, rope, nails, cloth and fish. Each secretly builds one project (boat, smokehouse, net, sledge, tent, cabin) worth 10–30 points. Accepted offers create agreements whose sides each owe goods.
>
> - For each speech and question, give the SET of option keys the speech asserts. Use ["none"] alone when the speech asserts none of them. Several options are allowed where the speech asserts several (e.g. two goods needed).
> - project: only the speaker's OWN project, stated or plainly implied by first person ("my net", "I'm building a sledge"). Projects of others do not count.
> - value: only a number of points the speaker gives for their OWN project, mapped to its band (low 10–16, mid 18–24, high 26–30). An odd number between bands: pick the band it falls in, rounding up (17 → mid, 25 → high).
> - need: goods the speaker says they need or want to acquire for themselves, including asking for or offering to buy a good ("I'm looking for rope", "rope 1 for 3 fish?" asks for rope). Goods they offer to give away are not needs. Fish counts only if they say they want fish.
> - accused: residents the speech says have not delivered goods they owe (an accusation or complaint about a missing delivery). A mere reminder that a delivery is due later is not an accusation unless it says it is late or was not made.
> - promise_to: residents the speaker says they WILL deliver or give goods to ("I'll send your rope this turn", "I will deliver O12"). A proposal or offer to trade is not a promise.
> - Be consistent across all speeches. When genuinely ambiguous, prefer ["none"].
