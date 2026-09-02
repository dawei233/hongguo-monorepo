# -*- coding: utf-8 -*-
"""批量修复 node_modules 缺失 .js 的包（沙箱过滤导致 npm 解压缺文件）"""
import tarfile, urllib.request, json, os, io, sys

pkgs = ['agent-base','combined-stream','commander','delayed-stream','es6-error','form-data',
        'function-bind','https-proxy-agent','ms','retry','stat-mode','temp','xmlbuilder']
ok = 0
log = []
for pkg in pkgs:
    try:
        ver = json.load(open(f'node_modules/{pkg}/package.json', encoding='utf-8'))['version']
        req = urllib.request.Request(f'https://registry.npmjs.org/{pkg}/{ver}', headers={'User-Agent':'Mozilla/5.0'})
        turl = json.load(urllib.request.urlopen(req, timeout=20))['dist']['tarball']
        req2 = urllib.request.Request(turl, headers={'User-Agent':'Mozilla/5.0'})
        tgz = urllib.request.urlopen(req2, timeout=40).read()
        t = tarfile.open(fileobj=io.BytesIO(tgz), mode='r:gz')
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
        ok += 1
        log.append(f'{pkg}@{ver} OK')
    except Exception as e:
        log.append(f'{pkg} FAIL: {type(e).__name__} {str(e)[:100]}')

with open('fix_log.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(log) + f'\n完成: {ok}/{len(pkgs)}\n')
print('done', ok)
