"""Create an isolated local ASR environment; print its interpreter for Settings."""
from pathlib import Path
import argparse
import subprocess
import sys
import venv

def main():
    parser=argparse.ArgumentParser();parser.add_argument('environment',type=Path);args=parser.parse_args()
    if sys.version_info[:2] not in {(3,10),(3,11),(3,12)}:parser.error('Use Python 3.10–3.12 for this tested runtime')
    dest=args.environment.expanduser().resolve()
    venv.EnvBuilder(with_pip=True).create(dest)
    python=dest/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    subprocess.run([str(python),'-m','pip','install','-r',str(Path(__file__).resolve().parents[1]/'watchrec-desktop/helpers/requirements.txt')],check=True)
    print('Select this Python in WatchRec Settings:');print(python)
if __name__=='__main__':main()
