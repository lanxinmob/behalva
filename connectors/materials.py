"""Local attachment extraction. Cloud authorization remains a separate step."""
import base64
import hashlib
from pathlib import Path
import tempfile
import indexer


def process(client, attachments, data):
    directory=Path(data)/'extraction'
    directory.mkdir(parents=True,exist_ok=True)
    for attachment in attachments:
        if attachment.get('extraction_status'):
            continue
        suffix=Path(attachment['name']).suffix.lower()
        if suffix not in indexer.EXTENSIONS:
            client.call('attachments/'+attachment['id']+'/extraction',{
                'state':'needs_input','message':'此文件没有可用的本地文字解析器；图片或扫描件需要另行提取文字。'})
            continue
        try:
            blob=client.call('attachments/'+attachment['id'])
            raw=base64.b64decode(blob['base64'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=attachment['sha256']:
                raise ValueError('Attachment digest mismatch')
            # Parsers use the normal file interfaces; remove only this owned
            # temporary object, never the user's original shared file.
            with tempfile.TemporaryDirectory(dir=directory) as temporary:
                path=Path(temporary)/('input'+suffix)
                path.write_bytes(raw)
                text=indexer.extract(path)
            if not text.strip():
                raise ValueError('No extractable text')
            source=client.call('documents',{'kind':'shared','uri':'attachment:'+attachment['id'],
                'title':attachment['name'],'body':text,'coverage':'Local attachment text extraction',
                'request_id':'extract:'+attachment['id']})
            client.call('attachments/'+attachment['id']+'/extraction',{
                'state':'indexed','source_id':source['id'],'message':'已建立本地文字索引，云端使用需另行授权。'})
        except (OSError,ValueError,RuntimeError):
            client.call('attachments/'+attachment['id']+'/extraction',{
                'state':'needs_input','message':'本地解析未完成；原附件保留，请检查格式或补充文字。'})
