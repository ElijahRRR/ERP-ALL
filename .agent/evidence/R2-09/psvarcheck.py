import re, sys, collections
doc = open(sys.argv[1], encoding='utf-8').read()
blocks = re.findall(r'```powershell\n(.*?)```', doc, re.S)
bad = 0
for n, b in enumerate(blocks, 1):
    # 剥掉整行注释与行尾注释——注释里提到的变量名不是真的变量
    code = "\n".join(re.sub(r'#.*$', '', ln) for ln in b.splitlines())
    names = re.findall(r'\$([A-Za-z_][A-Za-z0-9_]*)', code)
    if not names:
        continue
    groups = collections.defaultdict(set)
    for v in names:
        groups[v.lower()].add(v)
    clashes = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    print(f"[{'OK ' if not clashes else 'BAD'}] 块 {n}：{sorted(set(names))}")
    for k, v in clashes.items():
        bad += 1
        print(f"        ↳ **大小写碰撞**：{v} 在 PowerShell 里是同一个变量")
print("\n结论：", "代码中无大小写碰撞 ✅" if not bad else f"{bad} 处碰撞 ❌")
sys.exit(1 if bad else 0)
