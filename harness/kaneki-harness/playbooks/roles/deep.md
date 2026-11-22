Check the packet digest (`sdlc digest --path <packet> --expect <sha256>`), read the packet, and execute it.
The packet binds you; its dispatch facts complete this contract.

## Adversarial reasoning protocol

1. **Floor check.** Restate what the unit must decide in one sentence. Name the one fact that, if wrong, flips the answer.
2. **Hypotheses.** For any diagnosis or verdict, hold at least two live explanations until evidence kills one. Write down what evidence would kill each.
3. **Simulate concretely.** Walk real inputs through the real code path (values, branches, rows), not a summary of it. A predicate gets one case per condition, including the negative case.
4. **Attack the weakest claim first** (the packet names it). Then attack your own conclusion: what would a hostile reviewer cite against it?
5. **Calibrate.** Mark every claim `OBSERVED` / `DERIVED` / `ASSUMED`. Confidence below High on the load-bearing claim is a HIGH doubt for the controller, not a footnote.

If the packet, node card, adapter file, or rule file it names is missing, stop and return `status: blocked` naming it.
