#!/bin/bash
# 批量修复 node_modules 缺失 .js 的包
# 关键：tarball 下载到工作区内 .fix_tmp/（/tmp 在沙箱内不可见不可靠）
cd "C:/Users/12529/WorkBuddy/2026-08-18-17-02-22/hongguo-electron" || exit 1
mkdir -p .fix_tmp

PKGS="combined-stream delayed-stream es6-error form-data function-bind https-proxy-agent ms retry stat-mode temp xmlbuilder"
NODE="C:/Users/12529/.workbuddy/binaries/node/versions/22.22.2/node.exe"
PY="C:/Users/12529/.workbuddy/binaries/python/envs/hongguo/Scripts/python.exe"

for pkg in $PKGS; do
  VER=$("$NODE" -e "console.log(require('./node_modules/$pkg/package.json').version)" 2>/dev/null)
  [ -z "$VER" ] && echo "$pkg: 无法获取版本" && continue
  curl -s -m 40 -A "Mozilla/5.0" -L "https://registry.npmjs.org/$pkg/-/$pkg-$VER.tgz" -o ".fix_tmp/$pkg.tgz"
  if [ ! -s ".fix_tmp/$pkg.tgz" ]; then
    echo "$pkg: 下载失败"
    continue
  fi
  "$PY" - "$pkg" <<'PYEOF'
import tarfile, os, sys
pkg = sys.argv[1]
t = tarfile.open(fileobj=open(f'.fix_tmp/{pkg}.tgz','rb'), mode='r:gz')
dest = f'node_modules/{pkg}'
for m in t.getmembers():
    parts = m.name.split('/', 1)
    n = parts[1] if len(parts) > 1 else m.name
    if not n:
        continue
    out = os.path.join(dest, *n.split('/'))
    if m.isdir():
        os.makedirs(out, exist_ok=True)
    elif m.isfile():
        os.makedirs(os.path.dirname(out) or dest, exist_ok=True)
        with open(out, 'wb') as f:
            f.write(t.extractfile(m).read())
t.close()
print(f'{pkg}: 解压完成')
PYEOF
  rm -f ".fix_tmp/$pkg.tgz"
done
echo "批量修复完成"
