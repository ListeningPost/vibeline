## Variant: Type groups

### Design stance
Loop / Repeat / Cloud are sections, not chips. Heartbeat is the scan target inside each group.

### Key choices
- Layout: sticky type headers, rails nested under them
- Typography: IBM Plex Sans / Mono
- Color: same heartbeat rules as 002
- Interaction: fold a group; force state on the selected row

### Trade-offs
- Strong at: many cron jobs next to a few live loops
- Weak at: a session that is both cloud and repeat has to pick a home

### Best for
If Repeat volume will dwarf interactive loops.
