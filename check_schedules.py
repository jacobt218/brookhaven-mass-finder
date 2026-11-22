"""Conservative monitoring: report changed official parish pages, never guess Mass times."""
import json, re, hashlib, urllib.request, html.parser, pathlib, datetime
ROOT = pathlib.Path(__file__).parent
class Text(html.parser.HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','noscript'): self.skip+=1
    def handle_endtag(self, tag):
        if tag in ('script','style','noscript') and self.skip: self.skip-=1
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)
def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; ParishScheduleMonitor/1.0)'})
    with urllib.request.urlopen(req,timeout=20) as r:
        raw=r.read(1500000).decode('utf-8','replace')
    p=Text();p.feed(raw)
    return re.sub(r'\s+',' ', ' '.join(p.parts)).strip()
def main():
    data=json.loads((ROOT/'churches.json').read_text())
    oldfile=ROOT/'snapshots.json'; old=json.loads(oldfile.read_text()) if oldfile.exists() else {}
    new=dict(old); changed=[]; errors=[]
    for church in data:
        name,url=church['name'],church['url']
        try:
            body=get(url)
            if len(body)<100: raise ValueError('Too little readable page content')
            # Only a change alert. Homepage differences are NOT verified Mass changes.
            digest=hashlib.sha256(body.encode()).hexdigest()
            if name in old and old[name]!=digest: changed.append(name)
            new[name]=digest
        except Exception as exc: errors.append(f'{name}: {type(exc).__name__}: {exc}')
    oldfile.write_text(json.dumps(new,indent=2,ensure_ascii=False)+'\n')
    report=['# Parish website monitoring','',f'Checked: {datetime.datetime.now(datetime.timezone.utc).isoformat()}','',
            'IMPORTANT: A changed website is not proof of a changed Mass schedule. No Mass times were automatically edited.','',
            '## Websites with changed visible content',*(f'- {x}' for x in changed), '',
            '## Sites requiring attention',*(f'- {x}' for x in errors)]
    (ROOT/'report.md').write_text('\n'.join(report)+'\n')
    print('\n'.join(report))
if __name__=='__main__':main()
