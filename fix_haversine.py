# -*- coding: utf-8 -*-
with open('app/deps.py', 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("math.sqrt(1 - a)", "math.sqrt(max(0.0, 1 - min(1.0, a)))")
with open('app/deps.py', 'w', encoding='utf-8') as f:
    f.write(content)

with open('app/routers/stops.py', 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("math.sqrt(1 - a)", "math.sqrt(max(0.0, 1 - min(1.0, a)))")
with open('app/routers/stops.py', 'w', encoding='utf-8') as f:
    f.write(content)
