I'm a participant in an email by poker tournament, wrgpt. I'd like to write some software to make it easier to play and help me learn to be a better poker player.

## Ingestion

Game state and instructions happen by email. I've included an email thread here in the folder `example-hand` that shows what one of these threads look like. Note that there are general state emails that go to all players, and then an email that goes to only me ("your cards" or "reminder") that show my cards. 

I'd like to run a mail server on this machine. I'll send mails to this server, either by forwarding them from gmail or subscribing to another addressing wrgpt.

I'm looking to do other mail-processing type projects on this machine, so setting up the infrastructure to handle incoming emails and route them to different projects is useful. I own the kloninger.com domain today, and have it hosted by protonmail, but am not super happy with that setup. Initially I'd like this to exist separate from that (for example, distinct mx record on a new domain like home.kloninger.com)

## UI

I want the local app to maintain game state and show it in a nice web based UI. It can link out to the wrgpt canonical site for instructions and debugging.

The critical feature of this UI is to combine my own state with the game state, that's the main thing missing from the game state system e.g. https://hands.wrgpt.org/b/b17.html. but starting with that as an interface isn't terrible since it's familiar. 

I'd like to take action in the UI: check, fold, bet. Doing that will send mail on my behalf.

Sometimes I'll take action away from the computer, just sending email from my phone. wrgpt-buddy won't be party to that email, it'll just have to infer what happened when it sees an email showing progress or confirming action.

I'd like to use my local AI agent to analyze the state of the game. It should formulate a recommendation for my next move asynchronously, but only show it when asked. 

## Phasing

To start with let's do something passive, only adding the ability to send email and take action once I'm confident that this is working.

## Decisions (clarified 2026-10-15)

### Mail
- **Inbound, phase 1:** poll Gmail (a filter labels wrgpt mail, and the app reads that label). A self-hosted MX on home.kloninger.com, which can route mail to multiple projects, is a separate, later project.
- **Outbound, later phase:** send as sefklon@gmail.com, the address registered with the dealer, so replies thread with the ones sent from my phone.

### Recommendations
Run only on hands where I'm still in the pot. Start the analysis when an email shows it's my turn, and keep the result hidden until I ask for it.

1. **Local models:** ask two models served by LM Studio through its OpenAI-compatible endpoint: qwen3.6 (default `qwen/qwen3.6-35b-a3b`) and `openai/gpt-oss-20b`. Model names are configurable.
2. **Sonnet:** ask Sonnet the same question. All three run in parallel.
3. **Judge:** add both local answers as a second turn in the Sonnet conversation, reusing the prompt cache, and ask Sonnet to compare the three. Tell it explicitly that it may side with either local model.

All models return structured output: action, amount and reasoning. Concurrence is graded for each local model against Sonnet:
- **Agree:** same action and similar size.
- **Same action, different size:** for example, both bet but different amounts. Interesting, so show it.
- **Different action:** for example, fold vs. call.

Presentation: always show every explanation. When they disagree, highlight that and show the judge's evaluation.

### UI
- Reachable over Tailscale, including from my phone.
- Keep the full hand history.
- Opponent stats: maybe, later.
- Single table only.

### Email format notes
- Broadcast sequence numbers (`[b17.10014]`) are consecutive per table, across hands. A gap means a broadcast never arrived; hand 2 lost 10016 and 10025, probably during the Gmail delivery problems the dealer reported on 10/14. Every broadcast carries a full snapshot (pot, board, seat table, next to act), so current state stays correct even when one is lost. Only the action log has holes, and the end-of-hand history fills them. The UI flags hands with missing broadcasts. If this keeps happening, the `WHAT` command or hands.wrgpt.org can be used to recover.
- My replies are dealer commands, often sent in advance (`CALL *`, `MAKE $x`, `IF/B<=500 CALL *`, sticky `/S`, `/H=<hand>`). They go to dealer@wrgpt.org with `ID=<myid>` in the subject. The full syntax is in [docs/wrgpt-manual.txt][manual], fetched 2026-10-15.

[manual]: docs/wrgpt-manual.txt
