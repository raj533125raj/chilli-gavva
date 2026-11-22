# Button fix: door bell ni malli deploy cheyadam

Ee guide owner kosam. Inbox fix unna robot version main lo owner release chesaka ee Worker update cheyandi. Ee job code ni push cheyadu; mee account lo emi deploy cheyadu.

## Worker code copy-paste

1. Ee folder lo `deploy/cloudflare_alarm_worker.js` open cheyandi. File motham select chesi copy cheyandi.
2. Cloudflare dashboard lo mee account enchukoni **Workers & Pages** open cheyandi. Ippatike unna Chilli Gavva door bell Worker ni enchukondi; kotha Worker avasaram ledu.
3. **Edit code** nokkandi. Existing editor file motham replace chesi copy chesina JavaScript paste cheyandi. Code lo secret values rayakandi.
4. **Deploy** nokki kotha version live ayindani confirmation chudandi. Save maatrame chesunte, **Deployments** lo **Promote deployment** enchukoni aa kotha version ki 100 percent traffic ivvandi. Cloudflare dashboard lo editing, saving, promoting steps [official deployment guide](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/workers/versions-and-deployments/deployment-management.mdx) lo unnayi.

## Existing setup check

5. Worker **Bindings** lo `STATE` ane KV namespace binding undali. Ade existing namespace ni unchandi; stored clock/receipt records teeseyakandi. Binding lekapothe **Add binding**, **KV namespace** enchukoni Variable name `STATE` pettandi; mee existing namespace select chesi **Add binding** nokkandi. Ee dashboard steps [official KV guide](https://developers.cloudflare.com/kv/get-started/#3-bind-your-worker-to-your-kv-namespace) lo unnayi.
6. Worker settings lo existing secret names `GITHUB_TOKEN`, `WORKER_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` unnayo chudandi. Values ni ee file lo, chat lo, code editor lo paste cheyoddu. Existing secret values ni marchadam ee fix ki avasaram ledu.
7. Existing every-minute Cron Trigger undani chudandi. Mac worker active ga unte cloud run start cheyadu; Mac worker tana inbox cycle lo pani chestundi.

## Phone lo test

8. Owner Telegram chat lo `/buttontest` pampandi. Robot pampina kotha test button vachaka okkasari nokkandi. Paata message button ni ee test kosam vadakandi.
9. Door bell next minute tick lo aa press ni chuste spinner meeda `Andindi ✅ Robot ~10 min lo chestundi` ane acknowledgement ravali. Ee acknowledgement receipt maatrame; actual button pani robot run lo jarugutundi.
10. Robot next run ayyaka button-test result message ravali. GitHub Actions lo aa run inbox log lo `buttons_too_old=0 first_button_age_min=0` laanti counters chudandi. Actual numbers press age batti marutayi. `answerCallbackQuery` status called ayi undali; missing secret / active Mac / run timing unte `/doctor` tho status chudandi.
11. Already answered receipt ni Telegram malli accept cheyakapoyina robot decision pani chestundi; result message vastundi. Paata Draft leda expired proposal aithe `Ee button paatadi. Next: /drafts` vastundi. Appudu `/drafts` pampinchi kotha button enchukondi.

`first_button_age_min` ante first owner button unna message age. Telegram click timestamp ivvadu; timestamp lekapothe zero chupistundi. `buttons_too_old` ante Telegram reject chesina old acknowledgement receipts count. Logs lo IDs, message text, secrets undavu.

Next: Worker version live ayyaka `/buttontest` pampandi.
