"""Restore persistent data from market-cache Git branch, if it exists.

Only cache files (not website artifacts) are stored on this branch.
"""
from pathlib import Path
import subprocess
import tarfile
import io

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'.market-cache-state'
BRANCH='refs/remotes/origin/market-cache'


def git(*argv, check=True, **kwargs):
    return subprocess.run(['git','-C',str(ROOT),*argv],check=check,**kwargs)


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    p=git('fetch','--depth=1','origin','+refs/heads/market-cache:'+BRANCH,check=False,
          stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if p.returncode:
        print('首次启动：没有market-cache持久分支（或无权读取），准备新建缓存。',flush=True)
        return
    p=git('archive',BRANCH,stdout=subprocess.PIPE)
    # Archive of our own branch; verify extraction paths are confined to DEST.
    with tarfile.open(fileobj=io.BytesIO(p.stdout),mode='r:') as tf:
        for member in tf.getmembers():
            target=(DEST/member.name).resolve()
            if not target.is_relative_to(DEST.resolve()) or member.issym() or member.islnk():
                raise RuntimeError('缓存分支含不安全的归档路径')
        tf.extractall(DEST,filter='data')
    print(f'已恢复独立持久分支market-cache，缓存文件{len(list(DEST.rglob("*.gz")))}个',flush=True)

if __name__=='__main__':main()
