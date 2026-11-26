"""Read-only Pages setup check; missing owner setup is a successful, explained skip."""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

SETUP = 'Pages aapanu: Settings -> Pages -> Source lo GitHub Actions select cheyandi; docs/PAGES-TENGLISH.md chudandi.'


def check(env, fetch):
    if env.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or env.get('PUBLISH_APPROVED')!='true':
        return False,'Pages aapanu: owner Actions -> publish-content -> Run workflow lo Publish approved pages tick cheyandi.'
    if not env.get('DB_KEY','').strip():
        return False,'Pages aapanu: Settings -> Secrets and variables -> Actions lo DB_KEY setup cheyandi; key ni chat lo pampakandi.'
    repo=env.get('GITHUB_REPOSITORY','')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not env.get('GITHUB_TOKEN'):
        return False,'Pages aapanu: GitHub Actions token/repo setup check cheyandi; docs/PAGES-TENGLISH.md chudandi.'
    try:
        pages=fetch('/repos/'+repo+'/pages')
        if pages.get('build_type')!='workflow':return False,SETUP
        environment=fetch('/repos/'+repo+'/environments/github-pages')
        if environment.get('name')!='github-pages':
            return False,'Pages aapanu: Settings -> Environments lo github-pages create chesi main branch allow cheyandi.'
    except urllib.error.HTTPError as error:
        if error.code==404:return False,SETUP+' Environments lo github-pages kuda check cheyandi.'
        return False,'Pages aapanu: Actions permissions mariyu github-pages environment check cheyandi; docs/PAGES-TENGLISH.md chudandi.'
    except (OSError,ValueError,TypeError,AttributeError):
        return False,'Pages check ippudu avvaledu; owner tarvata Run workflow malli cheyandi.'
    return True,'Pages setup ready; owner confirm chesina approved pages maatrame build chestanu.'


def main(env=None, opener=None):
    env=os.environ if env is None else env
    opener=opener or urllib.request.urlopen
    def fetch(path):
        request=urllib.request.Request('https://api.github.com'+path,headers={
            'Authorization':'Bearer '+env.get('GITHUB_TOKEN',''),
            'Accept':'application/vnd.github+json','User-Agent':'chilli-pages-preflight'})
        with opener(request,timeout=10) as response:return json.load(response)
    ready,line=check(env,fetch)
    if env.get('GITHUB_OUTPUT'):
        with Path(env['GITHUB_OUTPUT']).open('a',encoding='utf-8') as stream:
            stream.write('ready='+str(ready).lower()+'\n')
    print(line)
    return 0


if __name__=='__main__':raise SystemExit(main())
