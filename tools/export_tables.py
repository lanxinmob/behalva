import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'connectors'))
from client import Client
from openpyxl import Workbook

p=argparse.ArgumentParser()
p.add_argument('--url',default='http://127.0.0.1:8765')
p.add_argument('--token',required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
tables=Client(a.url,a.token).call('state')['tables']
a.output.mkdir(parents=True,exist_ok=True)
for table in tables:
    stem=table['id']
    def cell(v):
        text=v if isinstance(v,str) else json.dumps(v,ensure_ascii=False)
        return "'"+text if text.startswith(('=','+','-','@')) else text
    book=Workbook();sheet=book.active;sheet.title='资料表'
    sheet.append([cell(v) for v in table['columns']])
    for row in table['rows']:sheet.append([cell(v) for v in row])
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    book.save(a.output/(stem+'.xlsx'))
    lines=['# '+table['title'],'','| '+' | '.join(table['columns'])+' |','| '+' | '.join('---' for _ in table['columns'])+' |']
    lines += ['| '+' | '.join(str(v).replace('|','\\|').replace('\n','<br>') for v in row)+' |' for row in table['rows']]
    (a.output/(stem+'.md')).write_text('\n'.join(lines)+'\n','utf-8')
print('Exported',len(tables),'tables.')
