"""Initialize and run the private workspace without a conversation window."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import ssl

ROOT=Path(__file__).resolve().parents[1]


def initialize(data):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import certifi
    data.mkdir(parents=True, exist_ok=True)
    config=data/'local.json'
    if not config.exists():
        config.write_text(json.dumps({'weekly_budget_usd':50,'ca_bundle':certifi.where(),
            'scan_roots':[]},indent=2),encoding='utf-8')
    cert_path,key_path=data/'server.pem',data/'server.key'
    if cert_path.exists()!=key_path.exists():
        raise RuntimeError('Incomplete TLS identity; preserve existing files and inspect before replacing')
    if not cert_path.exists():
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Personal Assistant')])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
            .sign(key,hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        if os.name!='nt':key_path.chmod(0o600)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert=x509.load_pem_x509_certificate(cert_path.read_bytes())
    pin=cert.fingerprint(hashes.SHA256()).hex()
    (data/'server-fingerprint.txt').write_text(pin+'\n',encoding='ascii')
    return config,cert_path,key_path


def supervise(data,host,port,browser):
    config,cert,key=initialize(data)
    logdir=data/'logs';logdir.mkdir(exist_ok=True)
    env=os.environ.copy()
    # Runtime dependency discovery is overridable, never persisted into source.
    if os.name=='nt':env['PATH']=env.get('PA_RUNTIME_BIN',r'D:\path\to\msys64\ucrt64\bin')+os.pathsep+env['PATH']
    children=[];logs=[]
    def spawn(command,name):
        log=open(logdir/(name+'.log'),'ab');logs.append(log)
        process=subprocess.Popen(command,env=env,stdout=log,stderr=log,cwd=ROOT)
        children.append(process);return process
    executable=ROOT/'build'/('assistant.exe' if os.name=='nt' else 'assistant')
    core_command=[str(executable),'--data',str(data),'--config',str(config),'--web',str(ROOT/'web'),
                  '--host',host,'--port',str(port),'--cert',str(cert),'--key',str(key)]
    url=f'https://127.0.0.1:{port}'
    worker_command=[sys.executable,str(ROOT/'connectors/worker.py'),'--data',str(data),
                    '--config',str(config),'--url',url]
    cfg=json.loads(config.read_text('utf-8-sig'))
    cfg['server_ca']=str(cert)
    # Worker settings live separately so operator-owned model settings stay intact.
    worker_config=data/'worker.generated.json'
    worker_config.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    worker_command[worker_command.index('--config')+1]=str(worker_config)
    try:
        core=spawn(core_command,'core')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))))
        for _ in range(100):
            if core.poll() is not None:raise RuntimeError('Core failed; inspect logs/core.log')
            try:opener.open(url+'/health',timeout=.2).close();break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('Core did not become healthy')
        adapters=[(spawn(worker_command,'worker'),worker_command,'worker')]
        if browser:
            cmd=[sys.executable,str(ROOT/'connectors/browser_worker.py'),'--data',str(data),'--url',url,'--ca',str(cert)]
            adapters.append((spawn(cmd,'browser'),cmd,'browser'))
        print('Workspace running at '+url,flush=True)
        while True:
            if core.poll() is not None:raise RuntimeError('Core stopped; supervisor exits instead of replaying unknown work')
            for i,(proc,cmd,name) in enumerate(adapters):
                if proc.poll() is not None:
                    time.sleep(3)
                    adapters[i]=(spawn(cmd,name),cmd,name)
            time.sleep(1)
    finally:
        for proc in reversed(children):
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        for log in logs:log.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['init','run'])
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--browser',action='store_true')
    args=parser.parse_args()
    if args.action=='init':
        initialize(args.data.resolve());print('Private TLS identity and settings are ready; no model credentials were created.')
    else:supervise(args.data.resolve(),args.host,args.port,args.browser)
