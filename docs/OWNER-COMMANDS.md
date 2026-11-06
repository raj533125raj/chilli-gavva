# Owner Commands Catalog for Voice and Prompt

The owner (speaking Tenglish and English) can control the robot and office via voice transcripts or typed prompts.
The offline parser in `owner_commands.py` translates free-form natural language into structured intents with arguments.

## Invariants and Safety
- **Offline parser only**: The parser never executes actions or connects to any network.
- **Risk classification**: Any potentially disruptive or state-altering action (`approve_draft`, `reject_draft`, `pause_all`, `resume_all`, `run_now`, `rename_agent`, `rename_room`, `move_room`, `resize_room`) sets `needs_confirm=True` requiring explicit confirmation before execution by downstream callers.
- **Privacy**: No secret credentials, passwords, or monetary amounts are hardcoded or leaked.
- **Fallback**: Unrecognized commands return `intent="unknown"` along with a friendly Tenglish hint.

---

## Catalog of Supported Intents

| Intent | Description | Needs Confirm | English Examples | Tenglish Examples |
|---|---|:---:|---|---|
| `approve_draft` | Approve a pending draft proposal by number | Yes | 1. "approve draft 59"<br>2. "approve 42"<br>3. "accept draft 10" | 1. "59 approve chey"<br>2. "draft 59 ni aamodinchu"<br>3. "okati approve cheyyi" |
| `reject_draft` | Reject a draft proposal with optional reason | Yes | 1. "reject draft 61, too generic"<br>2. "reject 12 reason weak niche"<br>3. "reject 44 because duplicate" | 1. "draft 61 reject chey, too generic"<br>2. "61 vaddu reject cheyyi reason weak niche"<br>3. "draft 12 tirooskarinchu, not good" |
| `list_drafts` | Show waiting draft proposals | No | 1. "list drafts"<br>2. "show drafts waiting"<br>3. "view pending drafts" | 1. "drafts chupinchu"<br>2. "pending drafts list chey"<br>3. "enni drafts unnay" |
| `show_status` | Show system / robot operational status | No | 1. "show status"<br>2. "system status"<br>3. "robot status report" | 1. "status chupinchu"<br>2. "robot status enti"<br>3. "office ela nadustondi" |
| `show_money` | Privately check balance / earnings report | No | 1. "show money"<br>2. "check earnings"<br>3. "show revenue balance" | 1. "paisalu chupinchu"<br>2. "entha vachindi"<br>3. "entha sampadincham" |
| `pause_all` | Emergency pause / stop all robot tasks | Yes | 1. "pause all"<br>2. "stop all"<br>3. "pause everything" | 1. "anni aapu"<br>2. "mottam aapandi"<br>3. "anni pause chey" |
| `resume_all` | Resume / unpause robot operations | Yes | 1. "resume all"<br>2. "unpause"<br>3. "start all tasks" | 1. "anni modalu pettu"<br>2. "malli start chey"<br>3. "anni resume chey" |
| `run_now` | Trigger an immediate robot work cycle | Yes | 1. "run now"<br>2. "execute now"<br>3. "trigger run immediately" | 1. "ippude run chey"<br>2. "cycle ippude start chey"<br>3. "ventane run chey" |
| `list_packs` | List ready upload packs | No | 1. "list packs"<br>2. "show upload packs"<br>3. "show ready packs" | 1. "packs chupinchu"<br>2. "upload packs list chey"<br>3. "ready packs anni chudu" |
| `show_pack` | View specific upload pack details / listing | No | 1. "show pack 3"<br>2. "view pack 2"<br>3. "display upload pack 5" | 1. "pack 3 chupinchu"<br>2. "pack 2 details choopinchu"<br>3. "moodu pack chudu" |
| `rename_agent` | Rename an agent | Yes | 1. "rename agent Scout to Finder"<br>2. "change agent Trend to Tracker"<br>3. "rename agent Doctor as Healer" | 1. "agent Scout peru Finder ga marchu"<br>2. "agent Scout ni Finder ga marchu"<br>3. "agent Trend peru Tracker ani marchu" |
| `rename_room` | Rename an office room | Yes | 1. "rename room Studio to Workshop"<br>2. "rename Pinterest room to PinLab"<br>3. "change room Reception to FrontDesk" | 1. "room Studio peru Workshop ga marchu"<br>2. "Studio room ni PinLab ga marchu"<br>3. "Pinterest room peru PinLab ga pettu" |
| `move_room` | Move room layout next to another room | Yes | 1. "move room Studio near Reception"<br>2. "move Pinterest room next to Council"<br>3. "move Reception room beside Studio" | 1. "Studio room ni Reception daggaraki jarupu"<br>2. "Pinterest room ni Council daggaraki pettu"<br>3. "Studio room Reception pakkana pettu" |
| `resize_room` | Make room bigger or smaller | Yes | 1. "resize room Studio bigger"<br>2. "make Pinterest room smaller"<br>3. "make Council room bigger" | 1. "Pinterest room ni pedda chey"<br>2. "Council room chinna chey"<br>3. "Studio peddadi cheyyi" |
| `theme` | Set theme to dark, light, or festival | No | 1. "theme dark"<br>2. "theme light"<br>3. "switch theme to festival" | 1. "dark mode pettu"<br>2. "light theme marchu"<br>3. "festival mode pettu" |
| `language` | Set language to English or Tenglish | No | 1. "language english"<br>2. "set language to english"<br>3. "switch language english" | 1. "tenglish lo matladu"<br>2. "bhasha tenglish pettu"<br>3. "language tenglish marchu" |
| `focus_room` | Focus / zoom view on a specific room | No | 1. "focus room Council"<br>2. "zoom into Pinterest room"<br>3. "focus on Studio" | 1. "Council room meeda focus pettu"<br>2. "Pinterest ni focus chey"<br>3. "Studio room meeda zoom chey" |
| `hide_money` | Hide or mask money figures on UI / views | No | 1. "hide money"<br>2. "mask earnings"<br>3. "don't show money" | 1. "screen meeda money daayi"<br>2. "paisalu daachipettu"<br>3. "money hide chey" |
| `show_money_on_screen` | Show money figures on main screen / wall | No | 1. "show money on screen"<br>2. "display money"<br>3. "unhide money on screen" | 1. "screen meeda paisalu chupinchu"<br>2. "screen lo money chupinchu"<br>3. "screen meeda money display chey" |
| `mute` | Silence voice / sound output | No | 1. "mute"<br>2. "silent mode"<br>3. "turn off sound" | 1. "sound aapu"<br>2. "silent ga undu"<br>3. "shabdam aapu" |
| `unmute` | Turn sound / voice output back on | No | 1. "unmute"<br>2. "turn on sound"<br>3. "enable audio" | 1. "sound on chey"<br>2. "unmute chey"<br>3. "shabdam on chey" |
| `help` | Show available commands and instructions | No | 1. "help"<br>2. "how to use"<br>3. "show command list" | 1. "sahaayam"<br>2. "commands chupinchu"<br>3. "em cheyagalanu" |
| `undo_last_ui_change` | Undo previous layout or theme change | No | 1. "undo last change"<br>2. "rollback ui"<br>3. "undo" | 1. "mundu laaga chey"<br>2. "venakki teesko"<br>3. "undo chey" |
| `daily_report` | Show formatted owner daily health report | No | 1. "daily report"<br>2. "show today's report"<br>3. "day report" | 1. "roju report chupinchu"<br>2. "daily report kavali"<br>3. "today report pettu" |
| `what_happened_today` | Summary of today's activities and events | No | 1. "what happened today"<br>2. "what did robot do today"<br>3. "today's summary" | 1. "ee roju em jarigindi"<br>2. "eroju updates enti"<br>3. "ee roju panulu enti" |
