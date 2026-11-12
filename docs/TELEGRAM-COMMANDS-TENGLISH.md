# Telegram Commands and Plain Phrases Guide (Tenglish House Style)

Ee document lo Chilli Gavva robot ki Telegram nunchi pampinche prati command mariyu plain English / Tenglish phrase vivarana undi.
Robot commands anni owner chat nunchi matrame accept chesthundi. Zero currency / zero money amounts rule ikkada strict ga follow avthundi.

Commands anni 8 mukhyamaina groups lo vibhajinchadam jarigindi:
1. **Drafts** — Draft ideas chudadaniki, aamodinchadaniki (approve) leda vaddu anadaniki (reject)
2. **Packs** — Ready upload packs chudadaniki mariyu listing details kosam
3. **Marketing** — Daily posts, Pinterest pins, Instagram reels, carousels, A/B titles
4. **Settings** — Quiet hours, morning post time, niches, run slots, auto-approval
5. **Reports** — Operations summary, HTML dashboard, SEO score, listing lint, keywords, changelog
6. **Office** — Office rooms layout, agent perulu marchadam, focus room, theme, sound
7. **Files** — Pack zip files, covers, pins, slides, landing page, launch checklist
8. **Health** — System status, doctor cases, pipeline progress, emergency pause, resume

---

## 1. Drafts Group

### `/list` (or `list`, `drafts`, `pending`, `list drafts`, `drafts chupinchu`)
Waiting drafts list chupisthundi. Prati draft ki Approve, Reject, View buttons untayi.
- **Example:** `/list` or `drafts chupinchu`
- **Reply preview:**
```
🌶️ 3 drafts waiting for review:
#42 ADHD Daily Focus Planner (Checker: 88/100)
#43 Telugu Wedding Checklist (Checker: 84/100)
#44 Student Revision Tracker (Checker: 79/100)

Buttons kindha unna options chusi decide cheyandi, leda /approve <ID> pampandi.
```

### `/view` (or `view <ID>`, `chudu <ID>`)
Oka nirdishtamaina draft yokka poorthi text mariyu sections chupisthundi.
- **Example:** `/view 42` or `view 42`
- **Reply preview:**
```
📄 Draft #42: ADHD Daily Focus Planner
Niche: Planners | Format: printable planner
Audience: ADHD Adults & Students

Listing Title: ADHD Daily Focus Planner Printable PDF
Description: Structured daily planner with visual focus blocks...
Tags: adhd planner, daily focus, printable tracker
Checker Review: 88/100 (Buyer clarity 20/20, Specificity 15/15)
```

### `/approve` (or `approve <ID>`, `aamodinchu <ID>`, `ok chey <ID>`, `42 ok`)
Draft ni aamodisthundi. Okate command lo enno IDs ivvavachu (`/approve 42 43`).
- **Example:** `/approve 42` or `approve 42`
- **Reply preview:**
```
✅ Draft #42 approved.
Product build queue lo add ayyindi. Next cycle lo builder robot start chesthundi.
```

### `/reject` (or `reject <ID>`, `vaddu <ID>`, `tirooskarinchu <ID>`, `42 vaddu`)
Draft ni vaddu ani reject chesthundi. Reject chesina ventane reason buttons chupisthundi.
- **Example:** `/reject 44` or `reject 44`
- **Reply preview:**
```
❌ Draft #44 rejected.
Reason emiti? Kindha unna buttons nunchi okati select cheyandi:
[Too generic] [Duplicate] [Weak niche] [Promises too much] [Other]
```

### `/decide` (or `decide`, `nirnayinchi`, `drafts decide`)
Reply keyboard tho okkoka draft ni serial ga review chesi aamodinchadaniki interactive flow start chesthundi.
- **Example:** `/decide` or `nirnayinchi`
- **Reply preview:**
```
📝 Draft #42 (1 of 3 waiting):
Title: ADHD Daily Focus Planner
Checker: 88/100 | Niche: Planners
Summary: Structured visual tracker with time blocks.

Select decision from keyboard:
[✅ 42] [❌ 42] [👁 42] [⏭ Next] [🛑 Stop]
```

---

## 2. Packs Group

### `/packs` (or `packs`, `ready packs`, `list packs`, `upload packs list`)
Store ki upload cheyadaniki ready ga unna upload packs list chupisthundi (10 packs per page).
- **Example:** `/packs` or `ready packs`
- **Reply preview:**
```
📦 Ready Upload Packs (Page 1):
1. Pack #1: adhd_daily_planner (v1.1) — Quality: READY
2. Pack #2: telugu_wedding_planner (v1.0) — Quality: READY
3. Pack #3: student_revision_tracker (v1.0) — Quality: READY

Total: 3 packs ready for manual store upload.
Details kosam `/pack <number>` ani pampandi.
```

### `/pack` (or `pack <ID>`, `show pack <ID>`)
Oka ready pack yokka complete store listing text, files list, deep QA verdict mariyu marketing status chupisthundi.
- **Example:** `/pack 1` or `pack 1`
- **Reply preview:**
```
📦 Pack #1: adhd_daily_planner
Status: READY | Deep QA: PASS
Files:
- product.zip (1.2 MB)
- cover.png (1280x720)
- listing.txt (Clean copy-paste text)
- checklist.txt

Marketing assets:
- Carousel: READY (5 slides)
- Pins: READY (5 pins)
- Reel: READY (4 frames)
- Blog: READY (blog.md)
```

### `/marketing` (or `marketing <ID>`, `mkt <ID>`)
Pack kosam generate aina marketing files (covers, pins, carousel, reels, blog) status chupisthundi.
- **Example:** `/marketing 1`
- **Reply preview:**
```
📢 Marketing Assets for Pack #1:
• Covers: 3 variants built (exported_content/marketing/pack_1/covers/)
• Pins: 5 Pinterest pin designs ready
• Carousel: 5 Instagram carousel slides ready
• Reel: 4 frames + shorts.md voiceover script ready
• Blog: blog.md ready for store post
```

---

## 3. Marketing Group

### `/post` (or `post today`, `ee roju posts`, `post tomorrow`, `post week`)
Social media lo post cheyalsina schedule chupisthundi leda ready photos / captions phone ki pampisthundi.
- **Example:** `/post today` or `post today`
- **Reply preview:**
```
📱 Ee Roju Social Posts (Today's Plan):
1. Pinterest Pin: "ADHD Focus Habit Tracker" (Pack #1)
   Caption: Plan your day with calm focus blocks... #adhdplanner #printable
2. Instagram Carousel: "5 Simple Steps for Daily Focus" (Pack #1)
   5 slides ready. Repost to Instagram.

Post chesaka `/posted 1` ani pampandi.
```

### `/posted` (or `posted <N>`, `post done <N>`, `post chesa <N>`)
Ee roju scheduled post number N complete ayyindi ani record chesthundi.
- **Example:** `/posted 1` or `post chesa 1`
- **Reply preview:**
```
✅ Post #1 marked as completed for today.
Social post planner tracker updated. Reposting summary saved!
```

### `/ab` (or `/ab <pack>`, `/ab <pack> <title 1-5> <clicks>`)
A/B title tests clicks track chesthundi mariyu leading title chupisthundi.
- **Example:** `/ab 1 2 45` (Pack 1, Title 2 received 45 clicks)
- **Reply preview:**
```
📊 A/B Title Tracking for Pack #1:
Title 1: 32 clicks
Title 2: 45 clicks 🏆 (Leader)
Title 3: 18 clicks
Leader: "ADHD Daily Focus Planner Digital Sheet"
```

### `/boards` (or `boards`, `pinterest boards`, `board plan`)
Pinterest board plan summary, board names mariyu pin counts chupisthundi.
- **Example:** `/boards`
- **Reply preview:**
```
📌 Pinterest Board Plan:
• Board 1: Daily ADHD Planners (8 pins assigned)
• Board 2: Student Study Trackers (6 pins assigned)
• Board 3: Minimalist Digital Prints (5 pins assigned)
Total: 3 boards planned across all ready packs.
```

---

## 4. Settings Group

### `/settings` (or `settings`, `robot settings`, `settings chupinchu`)
Robot settings chupisthundi mariyu quiet hours, morning post time, active niches configure chesthundi.
- **Example:** `/settings` or `settings chupinchu`
- **Reply preview:**
```
⚙️ Chilli Gavva Robot Settings:
• Quiet Hours: 22:00 - 08:00 IST (No alerts sent)
• Morning Post Time: 08:30 IST
• Active Niches: planners (ON), trackers (ON), wallart (ON)
• Run Slots: morning (ON), afternoon (ON), night (ON)
• Daily Message Cap: 50 messages

Change cheyadaniki: `quiet 23-07`, `morning 9`, `niche budget off`.
```

### `/autoapprove` (or `autoapprove`, `auto approve settings`)
Safe auto-approval settings (threshold score, daily limit, on/off) chusi update cheyadaniki.
- **Example:** `/autoapprove` or `autoapprove on`
- **Reply preview:**
```
🛡️ Auto-Approve Settings:
Status: ON
Score Threshold: 80/100 (Drafts >= 80 score waiting > 24h will auto-approve)
Daily Limit: 2 drafts per day
Safety Check: Duplicate similarity <= 0.45

Update cheyadaniki: `autoapprove 85` leda `autoapprove limit 3`.
```

### `/menu` (or `menu`, `options`, `commands`)
Grouped command menu chupisthundi.
- **Example:** `/menu`
- **Reply preview:**
```
📋 Chilli Gavva Menu (Mee robot commands):
*Drafts*:
  /list — Waiting drafts chupisthundi
  /view — Draft motham chadavadaniki
  /approve — Draft approve cheyadaniki
  /reject — Draft reject cheyadaniki
*Packs*:
  /packs — Ready ga unna packs list
  /pack — Pack details choodandi
...
Tip: Edhaina command vivarana kosam `/help <command>` type cheyandi.
```

### `/help` (or `/help <command>`, `sahayam`, `ela cheyali`)
Oka command gurinchi sampoorna vivarana mariyu keyword shortcuts chupisthundi.
- **Example:** `/help approve`
- **Reply preview:**
```
ℹ️ Command Help: /approve
Category: Drafts
Details (Telugu): Draft approve cheyadaniki: /approve <ID>
Details (English): Approve draft: /approve <ID>
Keywords: approve, aamodinchu, ok chey
```

---

## 5. Reports Group

### `/status` (or `status`, `paristhithi`, `robot status`, `system status`)
Compact system status chupisthundi (AI provider calls, drafts count, product builds).
- **Example:** `/status` or `robot status`
- **Reply preview:**
```
🤖 Chilli Gavva Status:
AI Providers: groq (OK), gemini (OK)
Drafts Today: 3 made, 1 waiting, 2 approved
Products Built: 2 READY, 0 NEEDS FIX
Pipeline: plan 1/1, research 2/2, drafts 2/2, builds 2/2
Last Run: 10 mins ago (All steps OK)
```

### `/ops` (or `ops`, `ops report`, `operations report`)
Weekly operations metrics (runs, failures, retries, providers) summary chupisthundi.
- **Example:** `/ops` or `ops report`
- **Reply preview:**
```
📊 Operations Weekly Summary:
• Total Runs: 168 runs
• Success Rate: 99.4% (1 failure auto-healed)
• Providers Used: groq (80%), cerebras (20%)
• Products Processed: 5 upload packs
```

### `/dashboard` (or `dashboard`, `ops dashboard`, `dashboard doc`)
Self-contained HTML operations dashboard file ni document ga Telegram lo pampisthundi.
- **Example:** `/dashboard` or `dashboard pampu`
- **Reply preview:**
```
Sending document: ops-dashboard.html (Self-contained HTML dashboard with charts).
Browser lo open chesi system metrics anni chusukovachu.
```

### `/seo` (or `/seo <N>`, `seo score <N>`, `seo check <N>`)
Pack N yokka 0-100 SEO score, listing tags strength mariyu top 3 fixes chupisthundi.
- **Example:** `/seo 1`
- **Reply preview:**
```
🔍 SEO Report for Pack #1:
Score: 92/100 (Strong title + full 13 tags)
Top 3 Recommendations:
1. Add secondary keyword 'digital printable' in description opening.
2. Ensure search tags have no duplicates.
3. First 160 characters describe core benefit clearly.
```

### `/lint` (or `/lint <N>`, `listing lint <N>`, `lint check <N>`)
Pack N listing format rules (PASS/WARN/FAIL), forbidden word checks mariyu first problem lines chupisthundi.
- **Example:** `/lint 1`
- **Reply preview:**
```
📋 Listing Lint Check for Pack #1:
Verdict: PASS ✅
Checks:
- Title length: 68 chars (PASS, <= 100)
- Description: 450 words (PASS)
- Search tags: 13 tags (PASS)
- Forbidden currency words: 0 found (PASS)
```

### `/faq` (or `/faq <N>`, `buyer faq <N>`, `faq pampu <N>`)
Pack N buyer FAQ markdown text leda document ga pampisthundi.
- **Example:** `/faq 1`
- **Reply preview:**
```
❓ Buyer FAQ for Pack #1 (ADHD Daily Focus Planner):
Q1: Idi ela download chesukovali?
A1: Purchase complete ainaka direct ga PDF download link vasthundi.
Q2: Tablet lo use cheyavacha?
A2: Avunu, GoodNotes, Notability leda normal print out thesukovachu.
```

### `/dup` (or `dup`, `dup check`, `duplicate check`, `saripolchu`)
Ready packs madhya content overlap (similarity > 0.5) unda ani check chesthundi.
- **Example:** `/dup`
- **Reply preview:**
```
🔍 Duplicate Content Check:
All packs analyzed with k=5 word shingles.
Result: No overlapping pairs over 0.5 Jaccard similarity.
Packs are completely unique and differentiated! ✅
```

### `/keywords` (or `keywords`, `top keywords`, `mukhyamaina padalu`)
Keyword bank nunchi high-demand top 20 keywords list chupisthundi.
- **Example:** `/keywords`
- **Reply preview:**
```
🔑 Top 20 Digital Product Keywords:
1. adhd daily planner
2. wedding checklist printable
3. student study tracker
4. habit tracker pdf
5. freelancer invoice template
...
```

### `/calendar` (or `calendar`, `calendar export`, `ics file`)
Social media post plan yokka `.ics` calendar file ni document ga pampisthundi.
- **Example:** `/calendar` or `calendar pampu`
- **Reply preview:**
```
Sending document: post_plan.ics
Mee Google Calendar leda Apple Calendar lo import chesukovachu.
```

### `/versions` (or `/versions <N>`, `changelog <N>`, `version vivaralu <N>`)
Pack N semantic version (e.g. v1.1) mariyu file hash changelog chupisthundi.
- **Example:** `/versions 1`
- **Reply preview:**
```
🏷️ Version Info for Pack #1:
Current Version: 1.1
Last Changed: 2026-11-08
Changes:
- Minor: Updated preview-2.png layout.
- Files SHA-256 verified.
```

### `/ideas` (or `ideas`, `scout ideas`, `idea score`)
Latest scout product ideas ni 0-100 scale meeda score chesi rank chupisthundi.
- **Example:** `/ideas`
- **Reply preview:**
```
💡 Top Scored Product Ideas:
1. Score: 89/100 — Telugu Wedding Planning Organizer (Indian edition)
2. Score: 84/100 — Remote Worker Ergonomic Habit Tracker
3. Score: 78/100 — Small Business Daily Cash Flow Tracker
```

### `/week` (or `week`, `ee vaaram`, `eevaaram`, `this week`)
Weekly summary report chupisthundi (drafts made, approved, rejected, upload packs ready, posts done).
- **Example:** `/week` or `ee vaaram`
- **Reply preview:**
```
🗓️ Ee Vaaram Weekly Performance Summary:
• Drafts created: 14
• Approved: 8 | Rejected: 4 | Waiting: 2
• Upload packs ready: 3
• Social posts completed: 12
• A/B Title Leaders: 2 packs tested
```

---

## 6. Office Group

### Office layout & room controls
Virtual office lo rooms resize, rename leda move cheyadaniki plain phrases.
- **Resize Room:**
  - **Example:** `make research room bigger` or `research room peddadi chey`
  - **Reply preview:** `📐 Research room size "bigger" ga update cheyadaniki request queue chesam.`
- **Rename Room:**
  - **Example:** `rename research room to discovery` or `research room peru discovery ga marchu`
  - **Reply preview:** `🏷️ Room peru "discovery" ga marchadaniki request queue chesam.`
- **Move Room:**
  - **Example:** `move marketing room next to design`
  - **Reply preview:** `🚚 Marketing room ni design pakkana move cheyadaniki request queue chesam.`
- **Focus Room:**
  - **Example:** `focus on build room` or `build room meeda focus pettu`
  - **Reply preview:** `🔍 Build room meeda camera focus set chesam.`

### Agent styling & controls
- **Rename Agent:**
  - **Example:** `rename agent scout to detective` or `agent scout peru detective ga pettu`
  - **Reply preview:** `🤖 Agent peru "detective" ga marchadaniki update queue ayindi.`

### Display, theme & sound controls
- **Theme:**
  - **Example:** `theme dark` or `raatri theme pettu`
  - **Reply preview:** `🌙 Office theme "dark" mode ki set chesam.`
- **Sound / Audio:**
  - **Example:** `mute` or `sound quiet`
  - **Reply preview:** `🔇 Sound muted. Silent mode active.`
  - **Example:** `unmute` or `sound on chey`
  - **Reply preview:** `🔊 Sound unmuted. Audio alerts active.`
- **Undo last UI change:**
  - **Example:** `undo` or `mundu laaga chey`
  - **Reply preview:** `↩️ Last office UI change successfully reverted.`

---

## 7. Files Group

### Pack files delivery to phone
Ready pack files ni direct ga Telegram document ga deliver chesthundi (45 MB safe cutoff).
- **`/files <N>` (or `files <N>`, `pack files <N>`, `pack <N> files pampu`):**
  - **Example:** `/files 1` or `pack 1 files pampu`
  - **Reply preview:** Sends `product.zip`, `cover.png`, `listing.txt`.
- **`/covers <N>` (or `covers <N>`, `covers pampu <N>`):**
  - **Example:** `/covers 1`
  - **Reply preview:** Sends 3 cover variants as Telegram media group.
- **`/pins <N>` (or `pins <N>`, `pins pampu <N>`):**
  - **Example:** `/pins 1`
  - **Reply preview:** Sends Pinterest pins (up to 10 images) + 5 A/B pin titles caption.
- **`/carousel <N>` (or `carousel <N>`, `slides pampu <N>`):**
  - **Example:** `/carousel 1`
  - **Reply preview:** Sends Instagram carousel slides as media group + caption text.
- **`/reel <N>` (or `reel <N>`, `reel frames <N>`):**
  - **Example:** `/reel 1`
  - **Reply preview:** Sends reel frame PNGs + shorts.md voiceover script.
- **`/blog <N>` (or `blog <N>`, `blog pampu <N>`):**
  - **Example:** `/blog 1`
  - **Reply preview:** Sends `blog.md` store article as document.
- **`/landing <N>` (or `landing <N>`, `landing page <N>`):**
  - **Example:** `/landing 1`
  - **Reply preview:** Sends `landing.zip` (index.html self-contained landing page) as document.
- **`/launch <N>` (or `launch <N>`, `launch checklist <N>`):**
  - **Example:** `/launch 1`
  - **Reply preview:** Sends `LAUNCH.md` manual store upload checklist as document.

---

## 8. Health Group

### `/doctor` (or `doctor`, `health`, `doctor report`)
Robot health diagnostics, open cases mariyu self-check results chupisthundi.
- **Example:** `/doctor`
- **Reply preview:**
```
🩺 Doctor Log: No cases recorded yet. All robot agents healthy! ✅
Database integrity: OK
Storage: Clean
AI Provider Quotas: Within safe limits
```

### `/pipeline` (or `pipeline`, `progress`, `pipeline progress`)
Prati stage lo daily plan vs done counts, backlog mariyu stuck items progress chupisthundi.
- **Example:** `/pipeline`
- **Reply preview:**
```
🚀 Daily Pipeline Progress:
Plan: 1/1 done
Research: 2/2 done
Drafts: 3/3 done
Product Builds: 2/2 done
Stuck items: 0
```

### `/pause` (or `pause`, `pause all`, `stop all`, `aapu`, `anni aapu`)
Emergency brake: AI calls mariyu kotha drafts generation aapi vesthundi. Telegram inbox commands inka pani chesthayi.
- **Example:** `/pause` or `pause all`
- **Reply preview:**
```
⏸ Paused. From the next run, no AI work and no new drafts.
I will still answer here. Send /resume to start again.
```

### `/resume` (or `resume`, `resume all`, `start all`, `nadipinchu`)
AI operations mariyu pipeline generation malli modalu peduthundi.
- **Example:** `/resume` or `resume all`
- **Reply preview:**
```
▶️ Resumed. Work starts again on the next run.
```

### `/buttontest`
Telegram button clicks robot ki cheruthunnayo ledo check cheyadaniki test button alert pampisthundi.
- **Example:** `/buttontest`
- **Reply preview:**
```
Button test:
[Click to test response]
```

### `/rules` (or `rules`, `niyamalu`, `active rules`)
Robot improver loop nerchukunna active product rules list chupisthundi.
- **Example:** `/rules`
- **Reply preview:**
```
📜 Active Learned Rules:
1. Always include at least 2 printable sample pages in planners.
2. Avoid vague audience labels; target specific user niches.
3. Keep descriptions factual and structured without exaggerations.
```

### `/cubes` (or `cubes`, `department cubes`)
Department cubes status (Research, Build, Marketing, Quality) mariyu controls chupisthundi.
- **Example:** `/cubes`
- **Reply preview:**
```
🏢 Department Cubes Status:
• Research Cube: Active (Scout, Trend)
• Build Cube: Active (Planners, Trackers, Art)
• Marketing Cube: Active (Pins, Carousels, Reels)
• Quality Cube: Active (Checker, Truth check)
```

---
*Owner Note:* Ee system eppatiki automate ga post cheyadu, sell cheyadu leda email send cheyadu. Prati action owner control lo untundi. Zero money amounts rule strict ga enforce cheyabaddadi.
