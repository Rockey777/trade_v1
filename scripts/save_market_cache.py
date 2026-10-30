"""Commit validated rolling data into an independent `market-cache` branch.

This runs even after bootstrap fails, so successful per-stock work can resume.
Never push synthetic data or incomplete data to Pages from this script.
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.market-cache-state'
WORK=ROOT/'.market-cache-push'


def git(*args,at=ROOT,check=True):
    return subprocess.run(['git','-C',str(at),*args],check=check,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)


def main():
    if not (STATE/'universe.json.gz').exists():
        print('RPS缓存为空，无需推送。',flush=True)
        return
    if WORK.exists():
        raise RuntimeError('缓存分支工作目录已存在，拒绝误覆盖')
    remote=git('show-ref','--verify','--quiet','refs/remotes/origin/market-cache',check=False).returncode==0
    try:
        git('worktree','add','--detach',str(WORK),'refs/remotes/origin/market-cache' if remote else 'HEAD')
        if remote:
            git('switch','-C','market-cache',at=WORK)
        else:
            git('switch','--orphan','market-cache',at=WORK)
        # Ensure branch contains *only* our cache files.
        for entry in WORK.iterdir():
            if entry.name=='.git':continue
            if entry.is_dir():shutil.rmtree(entry)
            else:entry.unlink()
        for entry in STATE.iterdir():
            dest=WORK/entry.name
            if entry.is_dir():shutil.copytree(entry,dest)
            else:shutil.copy2(entry,dest)
        git('config','user.name','github-actions[bot]',at=WORK)
        git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com',at=WORK)
        git('add','-A',at=WORK)
        if git('diff','--cached','--quiet',at=WORK,check=False).returncode!=0:
            git('commit','-m','chore: persist verified incremental market cache',at=WORK)
            p=git('push','origin','HEAD:refs/heads/market-cache',at=WORK,check=False)
            if p.returncode:raise RuntimeError('缓存分支推送失败：'+p.stderr[-1000:])
            print('已保存market-cache分支，下次更新可直接复用61日及1001日历史。',flush=True)
        else:
            print('缓存无变化，不重复提交。',flush=True)
    finally:
        if WORK.exists():
            git('worktree','remove','--force',str(WORK),check=False)
            if WORK.exists():shutil.rmtree(WORK)

if __name__=='__main__':main()
