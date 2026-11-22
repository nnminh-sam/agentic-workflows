Check the packet digest (`sdlc digest --path <packet> --expect <sha256>`), read the packet, and execute it.
The packet's role limits, forbidden actions, evidence discipline and return contract bind you; its dispatch facts complete this contract.

- Do exactly what the approved plan and the node card say, task by task. Do not redesign: when the plan
  is unclear or seems wrong, stop and return `status: blocked` with the question instead of guessing.
- On a fix round, change only what the listed findings name, and say per finding what changed and where.
- Run the stack's checks named by the node card before you return, and report each with its exit code.

If the packet, node card, adapter file, or rule file it names is missing or unreadable, stop and return `status: blocked` naming the missing item — never improvise a procedure.
