## Variant: Roster liveness

### Design stance
Type and heartbeat live in the name column only. The rail stays as it is today.

### Key choices
- Layout: existing Vibeline rails, unchanged track
- Typography: IBM Plex Sans / Mono
- Color: yellow idle, red stopped, green + elapsed running — roster only
- Interaction: click a row; force idle / running / stopped on the selected session

### Trade-offs
- Strong at: smallest incorporate (CSS + a type field)
- Weak at: you still hunt the live tip of a long rail

### Best for
First slice: `source` → LOOP / REPEAT / CLOUD, heartbeat next to the title.
