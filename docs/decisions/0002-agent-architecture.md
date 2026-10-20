# 0002. One conversation per resident, one strict act tool, no framework

- **Status:** Accepted.
- **Date:** 2026-10-18

## Context

A game is 48 to 54 decisions by six residents with private information. A resident should remember what it said and why, including its own lies, and every call should be a judgement, with bookkeeping left to code. A forced tool skips thinking on every model that accepts one, and Sonnet 5.5 rejects it. Thinking blocks stay valid only with an unchanged prefix, in the producing account, and for models that can read them.

## Decision

1. **Conversations.** Each resident has one append-only conversation per game. `system` and `tools` are fixed. Assistant content, thinking blocks included, goes back exactly as the provider sent it.
2. **One strict `act` tool.** Its fields are answers, deliveries, offers and say with structured claims; the schema follows the arm. The request sends `tool_choice: {type: "auto", disable_parallel_tool_use: true}`, never a forced tool.
3. **Reasoning.** Adaptive thinking with a summarized display and effort `medium`, fixed and never varied.
4. **Errors and quoted words.**
   - A kernel rejection returns as a `tool_result` with `is_error` and the exact fix.
   - Other residents' words arrive inside `tool_result` content, as quoted data. The exceptions are a resident's first turn and a turn after one whose last reply held no tool call: there is no `tool_use` to answer, so the receipt and events come as user text.
5. **Attempts.**
   - A turn gets 3 attempts, then the kernel's default action, which declines, holds, offers nothing and says nothing.
   - Every Anthropic reply with content is appended, a `max_tokens` stop included; its tool call, if any, gets an `is_error` result.
   - A refusal takes the default at once. A reply with no content stops the game.
   - The default's receipt answers the last unanswered `tool_use` at the next turn, or opens it as text when there is none.
   - There are no server-side fallbacks, so the seat's model always plays.
6. **Verification.** Anthropic calls send block binding in `"error"` mode, so any prefix edit fails loudly.
7. **No agent framework.**
8. **Layout: V4.** The state goes inside the `tool_result`, and a persistent one-line persona reminder follows as a system message each turn. The P0 probe tied all four cells: 36 of 36 first-attempt valid acts on Haiku in each, and no suspected injection. The rule in design §2.2 then kept the reminder and put the state inside the result. Sonnet's twin V3 was 12 of 12. P1 checks the reminder on Sonnet. The texts are the probe's.

## Consequences

- The game loop treats provider content as opaque. Adapters are pure render and parse functions, and CI asserts, as JSON values, that each request is the previous one plus its reply plus one new turn.
- Decision evals cut conversations, so they strip all thinking from frozen prefixes in every arm.
- On OpenAI, replaying reasoning items does not guarantee earlier-turn reasoning is read. The reasoning-memory mode is reported per model, and comparisons across providers carry that caveat.
- Medium effort may skip thinking, so the thinking rate is reported, not tuned.
