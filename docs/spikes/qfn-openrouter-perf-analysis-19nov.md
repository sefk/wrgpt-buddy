# Qwen Flash on OpenRouter: why the calls are slow

Spike, 2026-11-19. Outcome: nothing changed in the code, on purpose. Runs stay
consistent until the planned move to a local model, when the thinking levels
get tuned against these baselines.

## Question

Why do the OpenRouter calls to Qwen Flash (`qwen/qwen3.8-flash`, label
"Qwen Flash") take so long?

## Finding

Reasoning tokens. The model thinks before it answers, and that hidden thinking
is almost all of what it generates. Network, provider routing and prompt size
are not the problem.

- The prompt is small (about 905 tokens, 2.4k characters).
- Each call generates 5-8k completion tokens, and 98% of them are reasoning
  tokens (for example 8,278 of 8,462).
- Throughput is 50-90 tokens/s, served by the Alibaba provider.
- Wall time is therefore set by how long the model decides to think, which
  varies a lot from call to call.

## Baseline: what production runs looked like

From `Recommendation.steps` for the last ~40 recommendations (ids 259-297,
2026-11-14 to 2026-11-18):

- Flash took 10-283s per call. Roughly a third took more than 60s.
- The 283s call (id 283) ended in `JSONDecodeError: Expecting value: line 1
  column 1`, probably the reply was cut off or empty after a long think.
- Flash runs after the local models and before Sonnet, so a slow call delays
  the rest of the analysis.
- For comparison, Sonnet takes 4-12s and the judge 5-9s. The local models
  (Qwen, gpt-oss) take 3-40s depending on whether LM Studio is warm.

Current request settings (`poker/advice/local.py`, `ask()`): `max_tokens`
16000, `temperature` 0.3, strict JSON schema response format, no `reasoning`
parameter. `ADVICE_REMOTE_TIMEOUT_S` is 180.

## Experiment

Replayed the stored prompt of recommendation 297 (a 150s call) directly against
OpenRouter, with `usage: {include: true}` to get token counts and cost. Two or
three runs per setting, so treat the numbers as rough.

| `reasoning` setting          | wall time  | reasoning tokens | cost per call |
|------------------------------|------------|------------------|---------------|
| none (current)               | 69-165s    | 5-8k             | $0.003-0.004  |
| `{"effort": "low"}`          | 106-236s   | 10-12k           | $0.005-0.006  |
| `{"effort": "minimal"}`      | 23-28s     | about 2k         | about $0.001  |
| `{"enabled": false}`         | 2.5-3.7s   | 0                | about $0.0002 |

Notes:

- `effort: "low"` was slower and costlier than the default on this model, so
  it is not a useful middle setting. `minimal` is.
- With reasoning disabled the model answers in about 170 tokens at roughly the
  same tokens/s, so the whole difference is thinking length.
- Cost tracks tokens. Cutting reasoning also cuts the bill by 3-15x.

## Not measured

Quality. We did not compare the actions Flash picks at each setting against
Sonnet, the judge, or the player's actual moves. Faster is only better if the
opinions stay useful, and a model with no thinking is probably weaker at the
close decisions.

## Decision

Keep the current settings (reasoning at the model default). Reasons: runs stay
comparable with the existing history, and the plan in about a month is to move
to a QFN-class model running locally and tune thinking levels then.

## Follow-ups when tuning

- Compare each thinking level (disabled, minimal, default) against the prior
  runs, same prompts, on agreement with the judge and with the actual move, and
  on time.
- OpenRouter's reasoning controls go in the request payload as
  `"reasoning": {"enabled": false}` or `{"effort": "minimal"}`. A local server
  (LM Studio) will have its own switch, so the knob will differ.
- If the long tail matters before then, the cheap levers are a lower
  `ADVICE_REMOTE_TIMEOUT_S` or `max_tokens`. They cap the worst case without
  changing typical runs, but a capped call becomes an error instead of an
  opinion.
- The probe script that produced the table replays a stored prompt with
  `usage: {include: true}`. It was a scratch file (`.tmp/probe_flash.py`) and is
  not committed.
