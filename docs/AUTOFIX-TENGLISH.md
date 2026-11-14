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

## 8. Telisina lopam (known gap)

GitHub lo tests "chunked runner" tho run avutayi. Adi fail ayina test perlu `tests.` ane mundu maata tho print
chestundi, aa perlu summary ki ardham kaavu. Anduke asalu run lo alert `Problem: AssertionError: a check failed.`
ani matrame cheppochu (test peru lekunda). Auto-fix appudu kuda start avutundi, kani AI ki test peru teliyadu.
Idi `run_failure.py` lo chinna fix; lead ki report chesanu.
