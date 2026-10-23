"""Local-only desktop helpers: privacy defaults, durable AI, relay integration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / 'watchrec-desktop' / 'helpers'
sys.path.insert(0, str(HELPERS))
import tasks
import composition
import llm
import server_sync
import translation

@pytest.fixture
def settings(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'ai_enabled':True, 'llm_base_url':'http://127.0.0.1:1/v1', 'llm_model':'test', 'llm_api_key':''}))
    monkeypatch.setenv('WATCHREC_SETTINGS', str(path))
    return path


def test_helpers_do_not_load_model_at_import():
    code = 'import sys; import tasks,server_sync; assert "torch" not in sys.modules; assert "funasr" not in sys.modules'
    subprocess.run([sys.executable, '-c', code], cwd=HELPERS, check=True)


def test_ai_requires_explicit_opt_in(settings, monkeypatch):
    settings.write_text(json.dumps({'ai_enabled':False,'llm_base_url':'https://example.com','llm_model':'test'}))
    post=Mock(side_effect=AssertionError('unexpected network'))
    monkeypatch.setattr(llm.requests,'post',post)
    with pytest.raises(ValueError):llm._chat('system','user')
    post.assert_not_called()


def test_composition_resume_preserves_exact_source_and_completed_passages(monkeypatch):
    raw = '原文。' * 1300 + '  \n'
    chunks = composition.split_source(raw)
    assert ''.join(chunks)==raw
    calls=[]
    def chat(system,user,**kwargs):
        passage=json.loads(user)['本段原文'];calls.append(passage);return '整理：'+passage
    monkeypatch.setattr(llm,'_chat',chat)
    parts=[None]*len(chunks)
    def checkpoint(index,value,total):parts[index]=value
    assert composition.compose(raw,checkpoint=checkpoint,max_parts=1) is None
    assert parts[0] and parts[1] is None
    result=composition.compose(raw,resume_parts=parts,checkpoint=checkpoint)
    assert len(calls)==len(chunks) and result.count('整理：')==len(chunks)


def test_rejection_preserves_source_without_resubmitting(monkeypatch):
    chat=Mock(side_effect=llm.ContentRejectedError('refused'))
    monkeypatch.setattr(llm,'_chat',chat)
    saved=[]
    result=composition.compose('原文内容',checkpoint=lambda i,v,n:saved.append(v))
    assert '原文内容' in result and '原文保留' in result
    assert isinstance(saved[0],llm.PreservedSource) and chat.call_count==1


def test_enrichment_checkpoint_survives_network_failure(settings,tmp_path,monkeypatch):
    (tmp_path/'原始转写.txt').write_text('第一段。'*1000)
    (tmp_path/'job.json').write_text(json.dumps({'title':'测试录音'}))
    calls=[]
    def chat(system,user,**kwargs):
        calls.append(user)
        if len(calls)==2:raise llm.requests.ConnectionError('test disconnect')
        return '整理正文'
    monkeypatch.setattr(llm,'_chat',chat)
    monkeypatch.setattr(llm,'headline',lambda text:'测试标题')
    with pytest.raises(llm.requests.ConnectionError):tasks.enrich(tmp_path,report=lambda x:None)
    saved=tasks.read(tmp_path/'enrichment.json');assert saved['done']==1 and saved['parts'][0]=='整理正文'
    assert saved['error_kind']=='network' and not saved['full_text']
    result=tasks.enrich(tmp_path,report=lambda x:None)
    assert result['status']=='done' and result['headline']=='测试标题'
    assert calls.count(calls[0])==1
    assert (tmp_path/'原始转写.txt').read_text()=='第一段。'*1000


def test_stream_requires_completion_event():
    response=Mock();response.iter_lines.return_value=['data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}']
    with pytest.raises(llm.StreamInterruptedError):llm._read_stream(response,lambda text:None)


def test_translation_resume_and_outdated_source(settings,tmp_path):
    tasks.save(tmp_path/'enrichment.json',{'full_text':'First sentence. Second sentence.'})
    class Fake:
        calls=0
        def translate(self,text,target):
            self.calls+=1
            if self.calls==2:raise IOError('interrupted')
            return '译文'+text
    fake=Fake()
    with pytest.raises(IOError):translation.translate_recording(tmp_path,'zh',report=lambda x:None,translator=fake)
    state=tasks.read(tmp_path/'translations/zh.json');assert state['done']==1
    done=translation.translate_recording(tmp_path,'zh',report=lambda x:None,translator=fake)
    assert done['status']=='done' and fake.calls==3
    tasks.save(tmp_path/'enrichment.json',{'full_text':'New source.'})
    changed=translation.translate_recording(tmp_path,'zh',report=lambda x:None,translator=fake)
    assert changed['text']=='译文New source.' and list((tmp_path/'translations').glob('*-previous-*.json'))


@pytest.mark.parametrize('name',['../a','/a','a/../b','a\\b','CON.wav','a/','a\x00.wav','a:stream'])
def test_relay_rejects_unsafe_ids(name):
    with pytest.raises(ValueError):server_sync.validate_id(name)


def test_relay_resume_then_report_local_transcript(tmp_path):
    content=b'generated audio fixture'*100
    requests_seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            assert self.headers['Authorization']=='Bearer local-test-token'
            if self.path.startswith('/pending'):
                value=json.dumps([{'id':'test.wav','size_bytes':len(content)}]).encode();self.send_response(200);self.end_headers();self.wfile.write(value)
            else:
                offset=int(self.headers.get('Range','bytes=0-')[6:-1]);requests_seen.append(offset)
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length',str(len(content)-offset))
                if offset:self.send_header('Content-Range',f'bytes {offset}-{len(content)-1}/{len(content)}')
                self.end_headers();self.wfile.write(content[offset:])
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests_seen.append(body)
            self.send_response(200);self.end_headers();self.wfile.write(b'{"ok":true}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        client=server_sync.ServerClient(url,'local-test-token');download=tmp_path/'download';download.mkdir();(download/'test.wav.part').write_bytes(content[:20])
        assert client.download('test.wav',download,len(content)).read_bytes()==content
        assert requests_seen==[20]
        job=tmp_path/'sessions/test';job.mkdir(parents=True)
        tasks.save(job/'job.json',{'remote':{'id':'test.wav','server':url}})
        tasks.save(job/'transcription.json',{'finished':True,'raw':'local transcript'})
        (job/'原始转写.txt').write_text('local transcript')
        server_sync.sync_once(tmp_path,{'server_url':url,'server_token':'local-test-token'},lambda v:None)
        assert requests_seen[-1]['transcript']=='local transcript' and (job/'server-result.json').exists()
    finally:server.shutdown();server.server_close()
