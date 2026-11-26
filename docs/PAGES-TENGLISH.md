# Pages owner setup

Code audit: the old publish-content workflow stopped immediately with exit 1 when DB_KEY was absent. It also called configure-pages without checking whether Pages was enabled or its source was GitHub Actions. Either setup problem can explain an early failed run. The live run log was not available locally, so the exact remote cause is unverified.

Ippudu missing setup unte green skip avutundi. Log lo oka Tenglish instruction vastundi. Database key value, token leda private files log lo chupinchadu. Push valla publish avvadu.

1. GitHub lo chilli-gavva repo open cheyandi. Settings -> Pages ki vellandi. Build and deployment -> Source lo **GitHub Actions** select cheyandi.
2. Settings -> Environments ki vellandi. **github-pages** environment lekapothe create cheyandi. Deployment branches lo **main** allow cheyandi. Mee approval rules unte alage unchandi; owner approval ivvali.
3. Settings -> Secrets and variables -> Actions lo **DB_KEY** repository secret already unda check cheyandi. Leka wrong key unte owner daggara unna correct key tho GitHub lo update cheyandi. Key ni chat, issue, commit leda log lo pampakandi.
4. Settings -> Actions -> General lo workflows enable chesi required actions allow cheyandi. Organization policy Pages/id-token permissions block chesthe owner/admin policy check cheyali. Workflow token GitHub automatic ga istundi; token ni copy cheyakkarledu.
5. Actions -> **publish-content** -> **Run workflow** open cheyandi. Branch **main** enchukondi. Ready approved pages ni public ga pettali ani decide ayithe maatrame **Publish approved pages** tick chesi Run workflow nokkandi.
6. Tick cheyakapothe green skip. Pages/environment/key setup lekapothe green skip + log instruction. Setup fix chesaka owner malli manual ga run cheyali.
7. Setup correct unte approved records maatrame decrypt -> private products unpack -> approved page build -> deploy avutayi. Environment approval adigithe owner review chesi approve cheyandi. Unapproved drafts publish avvavu.

Wrong encrypted key, corrupt archive leda a real build/deploy error can still fail an explicitly confirmed run; those are real errors and are not reported as a successful deployment. This change does not enable Pages or create an environment for the owner. agent.yml is unchanged.
