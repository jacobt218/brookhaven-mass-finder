"""Conservative parish Mass-schedule discovery. Never edits published Mass times.

Uses Python's standard library; intended for the existing GitHub Actions workflow.
Reports candidate schedule evidence, not verified Mass times.
"""
import datetime
import hashlib
import html.parser
import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ParishScheduleMonitor/2.0; schedule research)'}
TIME = re.compile(r'\b(?:1[0-2]|0?[1-9])\s*(?::|\.)\s*[0-5]\d\s*(?:a\.?m\.?|p\.?m\.?)\b|\b(?:1[0-2]|0?[1-9])\s*(?:a\.?m\.?|p\.?m\.?)\b', re.I)
MASS = re.compile(r'\b(?:mass(?:es)?|eucharist|liturgy|worship schedule)\b', re.I)
DAY = re.compile(r'\b(?:sunday|saturday|weekend|vigil)\b', re.I)
LANGUAGE = re.compile(r'\b(?:spanish|espa[nñ]ol|korean|latin|vietnamese|portuguese|polish|french|english)\b', re.I)
SCHEDULE = re.compile(r'(mass|liturgy|worship|schedule|times|visit-us)', re.I)
MAX_PAGES = 4  # homepage + up to three promising pages per church


class PageParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.skip = 0
        self.href = None
        self.anchor = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip += 1
        if tag == 'a' and not self.skip:
            self.href = attrs.get('href')
            self.anchor = []
        if tag in ('p', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'br') and not self.skip:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'a' and self.href is not None:
            self.links.append((self.href, ' '.join(self.anchor)))
            self.href = None
            self.anchor = []
        if tag in ('script', 'style', 'noscript', 'svg') and self.skip:
            self.skip -= 1
        if tag in ('p', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4') and not self.skip:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
            if self.href is not None:
                self.anchor.append(data)


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=12) as response:
        final_url = response.geturl()
        content_type = response.headers.get('Content-Type', '').lower()
        raw = response.read(1_500_000)
    if 'pdf' in content_type or raw.startswith(b'%PDF'):
        raise ValueError('PDF schedule requires separate review')
    if 'html' not in content_type and 'text/plain' not in content_type:
        raise ValueError('Unsupported content type: ' + content_type[:60])
    parser = PageParser()
    parser.feed(raw.decode('utf-8', 'replace'))
    lines = [re.sub(r'\s+', ' ', line).strip() for line in ''.join(parser.parts).splitlines()]
    lines = [line for line in lines if line]
    if sum(map(len, lines)) < 100:
        raise ValueError('Too little readable page content')
    return final_url, lines, parser.links


def candidate_links(base, links):
    host = (urllib.parse.urlparse(base).hostname or '').lower().removeprefix('www.')
    found = {}
    for href, label in links:
        url = urllib.parse.urljoin(base, href).split('#', 1)[0]
        parsed = urllib.parse.urlparse(url)
        target_host = (parsed.hostname or '').lower().removeprefix('www.')
        if parsed.scheme not in ('http', 'https') or target_host != host:
            continue
        if re.search(r'\.(?:jpg|png|gif|zip|docx?|xlsx?)(?:$|\?)', parsed.path, re.I):
            continue
        score = 0
        text = (label + ' ' + parsed.path.replace('-', ' ')).lower()
        if 'mass' in text: score += 6
        if 'schedule' in text or 'times' in text: score += 4
        if 'worship' in text or 'liturgy' in text: score += 3
        if 'sacrament' in text: score += 1
        if score: found[url] = max(found.get(url, 0), score)
    return sorted(found, key=lambda url: (-found[url], url))[:MAX_PAGES - 1]


def evidence(lines):
    """Capture nearby text only; no automatic language or schedule assertions."""
    snippets = []
    for i, line in enumerate(lines):
        context = ' '.join(lines[max(0, i - 1):min(len(lines), i + 2)])
        if TIME.search(context) and MASS.search(context) and DAY.search(context):
            excerpt = context[:360]
            if excerpt not in snippets:
                snippets.append(excerpt)
        if len(snippets) >= 5:
            break
    return snippets


def main():
    churches = json.loads((ROOT / 'churches.json').read_text(encoding='utf-8'))
    oldpath = ROOT / 'snapshots.json'
    try:
        old = json.loads(oldpath.read_text(encoding='utf-8')) if oldpath.exists() else {}
    except (ValueError, OSError):
        old = {}
    new = dict(old)
    results = []
    for index, church in enumerate(churches, 1):
        name, homepage = church['name'], church['url']
        errors, pages, examples = [], [], []
        try:
            final_url, lines, links = fetch(homepage)
            pages.append((final_url, lines))
            extra = candidate_links(final_url, links)
        except Exception as exc:
            errors.append(f'Homepage: {type(exc).__name__}: {exc}')
            extra = []
        for url in extra:
            try:
                final_url, lines, _ = fetch(url)
                pages.append((final_url, lines))
            except Exception as exc:
                errors.append(f'{url}: {type(exc).__name__}: {exc}')
        for url, lines in pages:
            for snippet in evidence(lines):
                examples.append({'url': url, 'excerpt': snippet})
        # Store hashes only for successfully fetched pages. Legacy homepage hash
        # is deliberately replaced by a versioned record, not treated as a change.
        fingerprint = {url: hashlib.sha256('\n'.join(lines).encode()).hexdigest() for url, lines in pages}
        prior = old.get(name)
        changed = isinstance(prior, dict) and prior.get('version') == 2 and prior.get('pages') != fingerprint
        if pages:
            new[name] = {'version': 2, 'pages': fingerprint}
        status = 'candidate evidence found' if examples else ('pages reached; no schedule evidence' if pages else 'no pages reached')
        results.append({'name': name, 'status': status, 'pages': len(pages), 'evidence': examples[:8], 'errors': errors, 'changed': changed})
        print(f'[{index}/{len(churches)}] {name}: {status}; pages={len(pages)}; errors={len(errors)}', flush=True)
        time.sleep(0.15)

    oldpath.write_text(json.dumps(new, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reached = sum(r['pages'] > 0 for r in results)
    with_evidence = sum(bool(r['evidence']) for r in results)
    lines = [
        '# Parish Mass schedule discovery report', '', f'Checked (UTC): {now}',
        f'Churches: {len(churches)} | Homepages/pages reached for {reached} churches | Candidate schedule evidence for {with_evidence} churches', '',
        '**IMPORTANT:** Extracted text is NOT a verified Mass schedule. It may include weekday, holiday, non-English, or outdated Masses. No published times were changed.', '',
    ]
    for r in results:
        lines += [f"## {r['name']}", '', f"Status: {r['status']} | Pages reached: {r['pages']}" ]
        if r['changed']:
            lines.append('Page text changed since previous version-2 check; this is NOT proof of a schedule change.')
        if r['evidence']:
            lines += ['', 'Possible official-site schedule evidence (manual review required):']
            for item in r['evidence']:
                lines += [f"- Source: {item['url']}", f"  - Excerpt: {item['excerpt']}"]
        if r['errors']:
            lines += ['', 'Access issues:'] + [f'- {err}' for err in r['errors']]
        lines.append('')
    (ROOT / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'Finished: {reached}/{len(churches)} churches reached; {with_evidence} with candidate evidence. No Mass times changed.', flush=True)


if __name__ == '__main__':
    main()
