# Auto-fix — Telegram lo meeku em kanipistundi (Tenglish guide)

Ee page owner kosam. Robot run fail aithe "Auto-fix" ane pani ela jarugutundo, **Telegram lo meeku e message
vastundo**, meeru em cheyalo simple ga cheptundi. Ikkada unna prati message robot code lo unna asalu message
(test `tests/test_cc2_autofix_flow.py` ee page ni code tho compare chestundi).

Mukhyamaina rule: **Robot tanantata merge cheyadu.** Fix ni robot propose chestundi, tests run chestundi,
tarvata Telegram lo rendu buttons pampistundi. Merge cheyalo vaddo meere decide chestaru.

---

## 1. Mottham flow okka chupu lo

| Step | Em jarugutundi | Meeku Telegram lo |
|------|----------------|-------------------|
| 1 | Robot run okasari fail ayyindi | Emi raadu (okasari fail aithe robot malli try chestundi) |
| 2 | Venta ventane rendosari fail ayyindi | Alert message + chivarlo `Auto-fix: started` |
| 3 | AI ki chinna, safe summary pampistundi (step peru + fail ayina test perlu matrame) | Emi raadu |
| 4 | AI oka fix PR (proposal) open chestundi | Emi raadu |
| 5 | Robot aa PR ni check chestundi: safe files ena, regression test unda | Emi raadu |
| 6 | PR meeda tests run avutayi | Emi raadu (tests ayye varaku wait) |
| 7 | Tests pass | `Auto-fix ready: ...` + ✅ Merge / ❌ Close buttons |
| 8 | Meeru button tap chestaru | `Auto-fix merged.` leda `Auto-fix closed.` |
| 9 | Tarvata run green ayyindi | `fixed ✓` |

---

## 2. Alert message (rendosari fail ayinappudu)

Ila kanipistundi:

```
Chilli Gavva: run FAILED 2 times in a row.
Step: Run tests first
Problem: Failed tests: test_example.ExampleTests.test_something
Failing tests: test_example.ExampleTests.test_something
What to do: open the robot-fix-runbook, paste this message into Codex.
robot-fix-runbook: https://github.com/<owner>/<repo>/blob/main/docs/robot-fix-runbook.md
https://github.com/<owner>/<repo>/actions/runs/<run number>
Auto-fix: started
```

- `Step:` — robot lo e step fail ayyindo.
- `Problem:` — chinna, fixed wording. Private details (customer peru, file paths, keys) eppudu raavu.
- Chivari line chala mukhyam. Adi ee naalugu lo okati:

| Chivari line | Ardham | Meeru em cheyali |
|--------------|--------|------------------|
| `Auto-fix: started` | AI fix kosam try chestondi | Emi vaddu, wait cheyandi |
| `Auto-fix: not started — no GitHub token` | Robot ki GitHub permission ledu | Runbook follow avvandi |
| `Auto-fix: not started — already tried in the last 24 h` | Ee roju already oka kotha problem ki try chesindi (roju ki okkate) | Runbook follow avvandi leda repu chudandi |
| `Auto-fix: not started — setup/workflow problem needs a human` | Tool/setup problem (udaharanaki oka program missing). AI deenni fix cheyakudadu | Runbook follow avvandi |
| `Auto-fix: not started — GitHub refused the request` | GitHub request teesukoledu | Konchem sepu tarvata chudandi; malli vasthe runbook |

Ade problem malli malli fail aithe robot prati sari message pampadu — 6 gantalaki okasari matrame gurthu chestundi.

---

## 3. Fix ready ayinappudu (buttons)

AI fix chesi, aa fix meeda tests anni pass aithe ee message vastundi:

```
Auto-fix ready: Auto-fix: Run tests first. Tests passed.
[ ✅ Merge ]  [ ❌ Close ]
```

- **✅ Merge** tap cheste: robot malli okasari check chestundi (ade commit ena, tests green ena, safe files ena),
  tarvata merge chesi `Auto-fix merged.` ani cheptundi.
- **❌ Close** tap cheste: fix ni teesukodu, PR close chesi `Auto-fix closed.` ani cheptundi.
- Meeru emi tap cheyakapothe emi jaragadu. Robot tanantata merge cheyadu.

Paatha button ni malli tap cheste (leda fix maarina tarvata tap cheste) idi vastundi:

```
This auto-fix button is no longer current. Wait for the next test result.
```

Appudu emi cheyakandi; kotha message vache varaku wait cheyandi.

---

## 4. Fix pani cheyanappudu

| Message | Ardham | Meeru em cheyali |
|---------|--------|------------------|
| `Auto-fix didn't work - use the robot-fix-runbook.` | AI emi marchaledu, leda fix meeda tests fail ayyayi, leda fix safe kaadu (protected file muttukundi / regression test ledu), leda Merge chivari check lo fail ayyindi | `docs/robot-fix-runbook.md` open chesi, alert message ni Codex lo paste cheyandi |
| `Auto-fix tried 3 times, AI servers were busy. Use the robot-fix-runbook.` | AI servers busy. Robot 30 nimishala gap tho motham 3 sarlu try chesindi | Runbook follow avvandi, leda konni gantala tarvata chudandi |

AI servers busy aithe robot meeku cheppakunda 30 nimishala tarvata malli try chestundi (motham 3 sarlu).
Madhyalo run green aithe aa retry cancel avutundi.

---

## 5. Antha bagunnappudu

Problem poyi tarvata run pass aithe:

```
fixed ✓
```

Inka emi cheyanakkarledu.

---

## 6. Robot eppudu cheyani panulu (safety)

- Meeru ✅ Merge tap cheyakunda emi merge avvadu.
- AI ki velle summary lo step peru, fail ayina test perlu matrame untayi. Keys, tokens, customer details, money
  amounts eppudu vellavu.
- Ee files ni muttukune fix ni robot oppukodu: `.github/workflows/`, encrypted files (`.enc`), database files,
  `config.json`, `.env`, private products folder.
- Regression test (`tests/` lo kotha test) leni fix ni oppukodu.
- Fix PR robot account nundi, `main` ki, sari ayina title tho unte matrame lekka.
- Password, token, key, login adigithe AI aagipovali ani prompt lo rasi undi. Alanti step vasthe adi meeru matrame
  cheyali.

---

## 7. Idi nijam ga pani chestondani ela telusu?

`tests/test_cc2_autofix_flow.py` ee motham flow ni internet lekunda (offline) run chestundi: fail ayina run summary →
alert → AI prompt → fake AI proposal → result check → Telegram message → Merge button → `fixed ✓`.
GitHub, AI, Telegram anni test lo fake; robot code matram asalu code. Mac lo check cheyadaniki:

```
python3 -m unittest tests.test_cc2_autofix_flow
```

Asalu GitHub meeda oka practice run kavalante Telegram lo `/autofix_drill` undi (adi asalu AI ni vadutundi).

---

## 8. Drill table — robot tanantata em fix chestundi, deeniki meeru kavali

`tools/autofix_drill.py` robot code ni oka temporary copy lo 10 rakaluga kavalani paadu chestundi, tarvata asalu
auto-fix flow ni internet lekunda (offline) nadipistundi: 2 fail ayina runs → alert → AI fix → tests → Merge
button → tarvata run. Telegram, GitHub, AI fake; robot code matram asalu code. AI javabu "recorded" (mundu rasi
pettina sari ayina fix), kabatti ee drill AI telivini kaadu, **robot flow ni** test chestundi.

Result (2026-11-16): **10 lo 10 anukunnatte ayyayi.**

| # | Drill (code peru) | Em paadu chesam | Robot kanipettinda? | Evaru fix chestaru | Meeku Telegram lo |
|---|-------------------|-----------------|---------------------|--------------------|-------------------|
| 1 | `import_error` | Oka file leni peru ni import chestundi | Avunu: `test_... (could not load)` | **Robot** (AI fix + meeru Merge) | Alert + `Auto-fix: started`, tarvata Merge / Close buttons |
| 2 | `failing_test` | Product code lo oka tappu number, test fail | Avunu: test peru tho | **Robot** (AI fix + meeru Merge) | Alert + `Auto-fix: started`, tarvata buttons |
| 3 | `timeout` | Code aagipoyindi, test eppatiki ayipodu | Avunu: `test_... (timed out)` | **Robot** (AI fix + meeru Merge) | Alert + `Auto-fix: started`, tarvata buttons |
| 4 | `syntax_error` | Product file lo syntax tappu | Avunu: test peru + `Likely file:` | **Robot** (AI fix + meeru Merge) | Alert + `Auto-fix: started`, tarvata buttons |
| 5 | `control_copy` | Auto-fix copy list lo oka file missing | Avunu | **Meeru** (runbook) | Alert + `Auto-fix: not started — setup/workflow problem needs a human` |
| 6 | `bad_workflow_step` | Workflow step leni file ni run chestundi | Avunu: step peru tho | **Meeru** (runbook) | Alert + `Auto-fix: not started — setup/workflow problem needs a human` |
| 7 | `flaky_test` | Test okasari fail, tarvata run lo pass | Avunu (okka fail, silent ga) | **Evaru vaddu** (robot malli try chestundi) | Emi raadu |
| 8 | `money_string` | Public file (README) lo money amount | Avunu: test peru tho | **Robot** (AI fix + meeru Merge) | Alert + `Auto-fix: started`, tarvata buttons (amount eppudu message lo raadu) |
| 9 | `broken_builder` | Habit tracker builder crash | Avunu: `test_... (setup failed)` + `Likely file:` | **Robot** (AI fix + meeru Merge) | Alert + `Auto-fix: started`, tarvata buttons |
| 10 | `plugin_none` | Telegram plugin tana command ki javabu ivvadu | Avunu: test peru + `Likely file:` | **Robot** (AI fix + meeru Merge) | Alert + `Auto-fix: started`, tarvata buttons |

Simple ga:

- **Robot tanantata fix chesevi (7):** code lo tappulu — import, fail ayina test, timeout, syntax, money string,
  builder crash, plugin javabu. Ikkada kuda chivari Merge tap **meeru** cheyali. Robot tanantata merge cheyadu.
- **Meeru kavalsinavi (2):** workflow files (`.github/workflows/`) mariyu auto-fix copy list. AI ee files ni
  muttukokudadu (safety), anduke robot AI ni start cheyadu. `docs/robot-fix-runbook.md` chudandi.
- **Evaru emi cheyakkarlenidi (1):** okasari matrame fail ayye (flaky) test. Tarvata run pass aithe message raadu.

Mac lo ee drill ni malli run cheyadaniki (sumaru 2 nimishalu):

```
python3.11 tools/autofix_drill.py
```

Gurthunchukondi: asalu AI ichhe fix prati sari sari kakapovachu. Appudu `Auto-fix didn't work` vastundi, meeru
runbook follow avvali. Drill cheppedi: problem kanipettadam, sari ayina dari (robot / meeru), tests, buttons,
messages anni pani chestunnayi ani.

---

## 9. Prati auto-fix message lo oka Tenglish line

Auto-fix messages English lo unnayi (code vatini alage compare chestundi). Ippudu prati message chivarlo oka
Tenglish line vastundi — meeru em cheyalo, leda emi cheyakkarledu ani:

| Message | Chivarlo vache Tenglish line |
|---------|------------------------------|
| Alert + `Auto-fix: started` | Meeru ippudu emi cheyakkarledu: robot fix try chestondi. Fix ready ayyaka Merge button pampistundi. |
| Alert + `setup/workflow problem needs a human` | Idi setup/workflow problem, robot tanantata fix cheyadu. Meeru runbook chusi fix cheyali. |
| Alert + `already tried in the last 24 h` | Robot ee roju already okasari auto-fix try chesindi. Meeru runbook chudandi. |
| Alert + vere `not started` karanam | Auto-fix start avvaledu. Meeru runbook chudandi. |
| `Auto-fix ready: ...` | Fix ready ga undi, tests pass ayyayi. Merge tap cheste fix robot lo kalustundi; vaddu ante Close tap cheyandi. |
| `Auto-fix didn't work ...` | Auto-fix pani cheyaledu. Meeru runbook chudandi. |
| `Auto-fix tried 3 times ...` | AI servers busy ga unnayi, robot 3 sarlu try chesindi. Meeru runbook chudandi. |
| Paatha button | Ee button paathadi. Kotha message vache varaku wait cheyandi. |

---

## 10. Paatha lopam — ippudu fix ayyindi

Mundu GitHub lo fail ayina test perlu alert lo raaledu (`Problem: AssertionError: a check failed.` matrame).
Ippudu alert lo test peru vastundi, mariyu veelaithe `Likely file:` (e product file lo tappu undochu) kuda.
Inko fix: main paadayinappudu `Auto-fix ready` message + Merge / Close buttons raaledu; ippudu vastayi.
