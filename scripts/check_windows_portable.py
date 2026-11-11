"""Relocate the ZIP; audit and train via the installed app without tools/network."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile


class Inputs(HTMLParser):
    def __init__(self, markup, action=None):
        super().__init__()
        self.fields=[]
        self.links=[]
        self.action=action
        self.active=action is None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='form' and self.action is not None:
            self.active=attrs.get('action')==self.action
        if tag=='input' and self.active:self.fields.append(attrs)
        if tag=='a' and attrs.get('href'):self.links.append(attrs['href'])

    def handle_endtag(self, tag):
        if tag=='form' and self.action is not None:self.active=False

    def value(self,name):
        return next(item['value'] for item in self.fields if item.get('name')==name)

    def values(self):
        return {item['name']:item.get('value','') for item in self.fields
                if item.get('name') and item.get('type','text') not in ('checkbox','radio','file','submit')}


def audit_workflow(opener, base, destination):
    """Submit only the app's synthetic fixture to the disposable relocated app."""
    with opener.open(base+'/strategy-audits/new',timeout=10) as response:
        inputs=Inputs(response.read().decode(), '/strategy-audits/import')
    with opener.open(base+'/strategy-audits/sample.zip',timeout=10) as response:
        sample=response.read()
    # stdlib has no multipart form encoder. Names and filename are fixed here;
    # only the synthetic ZIP is binary, so preserve its bytes without MIME recoding.
    boundary='temperq-'+uuid.uuid4().hex
    parts=[]
    fields=dict(token=inputs.value('token'),name='Portable synthetic audit',adapter='auto',
                opening_empty='yes',opening_cash='10000')
    for name,value in fields.items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                      +value+'\r\n').encode('utf-8'))
    parts.extend([(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
        'filename="synthetic.zip"\r\nContent-Type: application/zip\r\n\r\n').encode('ascii'),
        sample,b'\r\n',f'--{boundary}--\r\n'.encode('ascii')])
    request=urllib.request.Request(base+'/strategy-audits/import',data=b''.join(parts),
        headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    with opener.open(request,timeout=30) as response:
        imported=urllib.parse.urlparse(response.url).path
        markup=response.read().decode()
    assert re.fullmatch('/strategy-audits/[0-9a-f]{32}',imported), imported
    assert '01 · 材料核对' in markup
    inputs=Inputs(markup,imported+'/analyze')
    fields=inputs.values()
    fields['same_run']='yes'
    request=urllib.request.Request(base+imported+'/analyze',data=urllib.parse.urlencode(fields).encode())
    with opener.open(request,timeout=30) as response:
        governance=urllib.parse.urlparse(response.url)
        markup=response.read().decode()
    assert governance.path=='/strategy-governance'
    identity=imported.rsplit('/',1)[-1]
    assert urllib.parse.parse_qs(governance.query).get('strategy')==[identity]
    links=Inputs(markup).links
    reports={urllib.parse.urlparse(link).path for link in links
             if re.fullmatch(re.escape(imported)+'/reports/[0-9a-f]{32}',urllib.parse.urlparse(link).path)}
    assert len(reports)==1,reports
    report=reports.pop()
    with opener.open(base+report,timeout=30) as response:
        markup=response.read().decode()
    assert '账目已核对' in markup and markup.count('class="audit-stage"')==15
    with opener.open(base+report+'/evidence.json',timeout=30) as response:
        evidence=response.read()
    result=json.loads(evidence)['result']
    assert result['modules']['accounting']['exceptions']==[]
    assert abs(result['modules']['attribution']['net_pnl']-183.9)<1e-6
    assert len(result['coverage'])==15
    for route in ('strategy-attribution','strategy-comparison','strategy-simulation'):
        with opener.open(base+'/'+route+'?strategy='+identity,timeout=30) as response:
            assert response.status==200
    with (destination/'audit-evidence.json').open('xb') as stream:stream.write(evidence)
    with (destination/'audit-report.html').open('x',encoding='utf-8') as stream:stream.write(markup)
    return dict(source='synthetic_fixture',periods=2,net_pnl=183.9,accounting_exceptions=0,
                coverage_stages=15,sample_sha256=hashlib.sha256(sample).hexdigest(),
                evidence_sha256=hashlib.sha256(evidence).hexdigest(),report_path=report,
                broker_source_authenticated=False)


def check(archive: Path, destination: Path, *, require_long_json_paths=False):
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Use a new destination: {destination}")
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not (destination / item.filename).resolve().is_relative_to(destination):
                raise ValueError("Archive path escapes destination")
        z.extractall(destination)
    app = destination / "TemperQ"
    manifest = json.loads((app / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files_sha256"].items():
        assert hashlib.sha256((app / name).read_bytes()).hexdigest() == digest, name
    # Applied only to this disposable extracted copy, in parent AND worker Python.
    blocker = app / "runtime/Lib/site-packages/sitecustomize.py"
    blocker.write_text('''import sys
def offline(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise OSError("Portable acceptance: external network disabled")
sys.addaudithook(offline)
''', encoding="utf-8")
    env = dict(os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
               PYTHONHOME="Z:/no-system-python", PYTHONPATH="Z:/no-source-checkout",
               PYTHONNOUSERSITE="1", PYTHONUTF8="1")
    installed = app/'runtime/Lib/site-packages/qwb'
    package_sources = list((app/'src/qwb').rglob('*.py'))
    assert package_sources
    for source in package_sources:
        assert source.read_bytes() == (installed/source.relative_to(app/'src/qwb')).read_bytes()
    lifecycle = subprocess.run([str(app/'runtime/python.exe'), '-E', '-s', '-X', 'utf8',
        str(app/'scripts/verify_corporate_actions_e2e.py'), '--installed',
        '--out', str(destination/'lifecycle')], cwd=app, env=env,
        capture_output=True, encoding='utf-8', timeout=240)
    (destination/'lifecycle.stdout.txt').write_text(lifecycle.stdout, encoding='utf-8')
    (destination/'lifecycle.stderr.txt').write_text(lifecycle.stderr, encoding='utf-8')
    assert lifecycle.returncode == 0, lifecycle.stderr[-2000:]
    lifecycle_results = {name: json.loads((destination/'lifecycle'/name/'acceptance.json').read_text(encoding='utf-8'))
                         for name in ('split', 'bonus')}
    assert all(r['status']=='passed' and r['installed_package'] for r in lifecycle_results.values())
    if require_long_json_paths:
        assert all(r['json_paths_over_260_read_back'] > 0 for r in lifecycle_results.values())
    runtime_probe = '''import ctypes, json
from pathlib import Path
import sys
import lightgbm
from qwb import starter_models
cards=starter_models.catalog()
assert 'public40_lgbm_v1' in cards, 'Bundled model missing from installed package'
starter_models.verify(starter_models.BUNDLED/'public40_lgbm_v1')
kernel=ctypes.windll.kernel32
kernel.GetModuleHandleW.restype=ctypes.c_void_p
kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p]
kernel.GetModuleFileNameW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint]
loaded={}
for name in ['msvcp140.dll','vcomp140.dll']:
    handle=kernel.GetModuleHandleW(name)
    assert handle, name
    buffer=ctypes.create_unicode_buffer(32768)
    assert kernel.GetModuleFileNameW(handle,buffer,len(buffer))
    assert Path(buffer.value).resolve().parent == Path(sys.executable).resolve().parent, buffer.value
    loaded[name]=buffer.value
print(json.dumps(loaded,ensure_ascii=False))
'''
    dlls = subprocess.check_output([str(app / "runtime/python.exe"), "-E", "-s", "-X", "utf8", "-c", runtime_probe],
                                   cwd=app, env=env, encoding="utf-8", timeout=30)
    cli = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "temperq.bat", "--help"],
                         cwd=app, env=env, capture_output=True, encoding="utf-8", timeout=30)
    assert cli.returncode == 0 and "temperq" in cli.stdout.lower(), cli.stderr
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(route):
        return opener.open(base + route, timeout=10).read().decode()
    def post(route, form):
        return opener.open(urllib.request.Request(base + route, data=urllib.parse.urlencode(form).encode()), timeout=10)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    started = time.monotonic()
    with (destination / "startup.log").open("wb") as log:
        process = subprocess.Popen([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "start.bat",
                                    "--no-browser", "--port", str(port)], cwd=app, env=env,
                                   stdout=log, stderr=log, creationflags=flags)
        try:
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError((destination / "startup.log").read_text(encoding="utf-8", errors="replace"))
                try:
                    page = get("/")
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise TimeoutError("Portable startup timed out")
            assert "TemperQ" in page and "策略治理" in page and '/strategy-audits/new' in page
            assert len(get('/static/strategy-audit.css'))>1000
            audit_result=audit_workflow(opener,base,destination)
            assert "选择起步模板" in get('/start')
            assert "低波稳健" in get('/playbooks')
            assert "使用入门模型" in get("/strategies/new")
            assert len(opener.open(base + "/static/dm-sans.ttf").read()) > 1000
            assert len(get("/static/deployment.js")) > 1000
            assert len(get("/static/deployment.css")) > 1000
            assert len(get('/static/rule-editor.js')) > 1000
            rule_check = subprocess.run([str(app/'runtime/python.exe'),'-E','-s','-X','utf8','-m','qwb.cli',
                'rule','validate','examples/rule_card_v1.json','--json'],cwd=app,env=env,
                capture_output=True,encoding='utf-8',timeout=30)
            assert rule_check.returncode==0 and json.loads(rule_check.stdout)['valid'],rule_check.stderr
            assert (app/'AGENTS.md').is_file()
            page = get('/train')
            assert "一键下载免费数据（BaoStock）" in page
            inputs=Inputs(page)
            token = inputs.value('token')
            fields = dict(token=token, name="portable_acceptance", market="cn_a", horizon="5",
                          universe="all", capital="lt30w", model="lightgbm", dataset="__synthetic__",
                          num_boost_round="40", n_seeds="1")
            # Use the actual synthetic dataset value rendered by this version.
            fields["dataset"] = inputs.value('dataset')
            with post("/run", fields) as response:
                job_url = response.url
                body = response.read().decode()
            assert "/job/" in job_url, body[-2000:]
            job_id = job_url.rsplit("/", 1)[-1]
            job = app / "runs/jobs" / job_id
            for _ in range(240):
                status = json.loads((job / "status.json").read_text(encoding="utf-8"))
                if status["state"] not in ("queued", "running"):
                    break
                time.sleep(0.5)
            assert status["state"] == "succeeded", status
            report = get("/job/" + job_id)
            assert "一句话结论" in report and "完整报告" in report
            assert (app / "runs/portable_acceptance/final_model/meta.json").is_file()
            output = {"status": "PASS", "source_commit": manifest["source_commit"],
                      "microsoft_runtime_loaded_from": json.loads(dlls),
                      "relocated_to": str(app), "startup": "start.bat", "external_network": "blocked by Python audit hook",
                      "path": env["PATH"], "user_site": "disabled", "gpu": "not bundled",
                      "detached_training_job": job_id, "elapsed_seconds": round(time.monotonic() - started, 2),
                      "strategy_audit": audit_result,
                      "installed_package_python_files_matched": len(package_sources),
                      "corporate_action_lifecycle": lifecycle_results,
                      "long_json_paths_required": require_long_json_paths,
                      "bundled_starter": "installed package files verified; visible in model selection",
                      "deployment_assets": "local dashboard script and styles served from relocated app",
                      "rule_interface": "four templates, editor asset, bundled example, read-only CLI, AGENTS.md",
                      "limit": "Same Windows host; fresh-machine CI and nondeveloper trials remain separate acceptance."}
            (destination / "acceptance.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)
        finally:
            # Only the process tree created above, never other user services.
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            process.wait(timeout=30)
            blocker.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--require-long-json-paths", action="store_true")
    args = parser.parse_args()
    check(args.archive, args.destination, require_long_json_paths=args.require_long_json_paths)
