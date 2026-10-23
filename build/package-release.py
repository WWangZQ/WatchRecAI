"""Build source/VPS archives from a reviewed Git file allowlist, never runtime directories.

Run: python build/package-release.py [--version VERSION]
Desktop installers are built separately with Tauri; this script never packages an
arbitrary local app bundle or Python environment.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DIRS={'watchrec-desktop','watchrec-vps','watchrec-watch','docs','build','tests','.github'}
FILES={'README.md','AGENTS.md','.gitignore','.gitattributes','CHANGELOG.md','THIRD_PARTY_NOTICES.md','LICENSE'}
EXCLUDED={'node_modules','target','dist','gen','.git','.gradle','__pycache__','.venv','private','data','downloads','uploads','recordings','.verification','.build-env','.build-tools','.build-output'}
FORBIDDEN_SUFFIXES={'.wav','.m4a','.mp3','.aac','.flac','.ogg','.mp4','.webm','.pem','.key','.p12','.jks','.keystore','.log','.pyc','.db','.sqlite'}
TEXT_SUFFIXES={'.rs','.py','.ts','.js','.json','.toml','.html','.css','.kt','.kts','.sh','.ps1','.yaml','.yml','.xml','.properties','.txt'}
# These strings must never occur in implementation files. The audit itself and
# boundary documentation intentionally name the exclusions.
PRIVATE_MARKERS=[b'wetype',b'wechat', '微信'.encode(), b'top.willy.watchrec.next', b'/Users/willy/', b'token-plan-cn.xiaomimimo.com',b'2moro.top']

def candidates():
    names=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=ROOT).decode().split('\0')
    return sorted({name for name in names if name and (ROOT/name).is_file()})

def validate(name):
    p=Path(name)
    if p.parts[0] not in DIRS and name not in FILES:return False
    if any(part in EXCLUDED for part in p.parts):return False
    if p.suffix.lower() in FORBIDDEN_SUFFIXES:return False
    if p.name.startswith('.env') and p.name!='.env.example':return False
    if p.name in {'settings.json','local.properties','protocol.json','identity.json','server.crt'}:return False
    if (ROOT/p).is_symlink():raise ValueError(f'Symlink excluded from release: {name}')
    if p.parts[0]=='watchrec-desktop' and p.suffix in TEXT_SUFFIXES:
        text=(ROOT/p).read_bytes().lower()
        for marker in PRIVATE_MARKERS:
            if marker.lower() in text:raise ValueError(f'Private implementation marker in {name}')
    return True

def archive(path,names):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name in names:z.write(ROOT/name,'WatchRec/'+name)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--version',default='1.2.0-preview');args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',args.version):parser.error('Invalid version')
    names=[n for n in candidates() if validate(n)]
    assert 'watchrec-desktop/app/src/main.ts' in names
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    source=out/f'WatchRec-Source-{args.version}.zip';vps=out/f'WatchRec-VPS-{args.version}.zip'
    archive(source,names);archive(vps,[n for n in names if n.startswith('watchrec-vps/') or n in {'THIRD_PARTY_NOTICES.md'}])
    (out/'SHA256SUMS.txt').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in [source,vps]))
    print(f'Audited {len(names)} files; wrote {source.name} and {vps.name}')
if __name__=='__main__':main()
