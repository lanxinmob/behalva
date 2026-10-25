"""Read-only research tools. Model calls and source permission stay in the core."""
import re
from urllib.parse import quote, urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup

def discover(query, limit=5):
    r = requests.get('https://html.duckduckgo.com/html/', params={'q': query},
                     headers={'User-Agent': 'Mozilla/5.0 (PersonalAssistant research)'}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    links = []
    for item in soup.select('a.result__a'):
        href = item.get('href', '')
        if 'uddg=' in href:
            href = parse_qs(urlparse(href).query).get('uddg', [''])[0]
        if href.startswith('https://') and href not in links:
            links.append(href)
        if len(links) >= limit:
            break
    if not links:
        raise RuntimeError('Search returned no usable results; possible access challenge. Add source URLs or configure another search adapter.')
    return links

def markdown_tables(text):
    title = '调查表'
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith('#'):
            title = lines[i].lstrip('# ').strip()
        if i + 1 < len(lines) and lines[i].lstrip().startswith('|') and re.match(r'^\s*\|?[\s:|\-]+\|\s*$', lines[i+1]):
            cols = [v.strip() for v in lines[i].strip().strip('|').split('|')]
            rows = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                row = [v.strip() for v in lines[i].strip().strip('|').split('|')]
                if len(row) == len(cols):
                    rows.append(row)
                i += 1
            if rows:
                yield {'title': title, 'columns': cols, 'rows': rows}
        else:
            i += 1

def run(client, task):
    source_ids = list(task.get('source_ids', []))
    urls = task.get('urls', [])
    notes = []
    if not urls and not source_ids:
        # Avoid using the complete private profile as a search query.
        urls = discover(task['title'][:250])
    for url in urls[:10]:
        try:
            source = client.call('web/fetch', {'url': url})
            source_ids.append(source['id'])
        except Exception as e:
            notes.append({'url': url, 'error': str(e)})
    if not source_ids:
        return 'waiting_input', {'message': '没有取得足够的原文，请提供可读取的链接或选择本地资料。', 'coverage_gaps': notes}
    source_ids = list(dict.fromkeys(source_ids))
    result = client.call('model/run', {'task_id': task['id'], 'attempt_id': task['attempt_id'], 'source_ids': source_ids})
    artifact_key = result['run_id']
    table_ids = []
    for index, table in enumerate(markdown_tables(result['body'])):
        table['task_id'] = task['id']
        table['request_id'] = artifact_key + ':table:' + str(index)
        table_ids.append(client.call('tables', table)['id'])
    complete = result.get('completion_status') in ('completed', 'stop')
    state = 'completed' if complete and result['citation_status'] == 'references_validated' else 'waiting_input'
    return state, {'draft_id': result['draft_id'], 'table_ids': table_ids, 'citations': result['citations'],
                   'citation_status': result['citation_status'], 'invalid_citations': result['invalid_citations'],
                   'completion_status': result.get('completion_status'),
                   'coverage_gaps': notes, 'note': 'AI 草稿；引用标识已核验，事实判断仍需结合原文审阅。'}
