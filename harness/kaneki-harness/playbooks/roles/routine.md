Check the packet digest before reading it: `sha256sum "<packet>"` (Bash) or `(Get-FileHash -Algorithm SHA256 "<packet>").Hash.ToLower()` (PowerShell) must print the sha256 of your prompt (the `sdlc` driver is not on PATH; the packet's Driver section names it), read the packet, and execute it.
The packet's role limits, forbidden actions, evidence discipline and return contract bind you; its dispatch facts complete this contract.

If the packet, node card, adapter file, or rule file it names is missing or unreadable, stop and return `status: blocked` naming the missing item — never improvise a procedure.
