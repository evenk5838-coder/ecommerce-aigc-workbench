"""Render the recorded JSON as a clearly labeled report image, not a UI screenshot."""
from pathlib import Path
import hashlib
import json
import re
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
SOURCE = BASE / 'run-20261027T183058-78791/节点执行与撤销-重启HTTP.json'
LOG = BASE / 'verify-20261027T183058-78791.txt'
data = json.loads(SOURCE.read_text(encoding='utf-8'))
log = LOG.read_text(encoding='utf-8')
unit = int(re.search(r'Ran (\d+) tests', log).group(1))
retained = int(re.search(r'结果：(\d+)/\d+ 通过', log).group(1))
fonts = [Path('/System/Library/Fonts/PingFang.ttc'), Path('/System/Library/Fonts/STHeiti Light.ttc')]
font_path = next((p for p in fonts if p.is_file()), None)
if font_path is None:
    raise RuntimeError('Chinese system font not found; do not render unreadable evidence.')
font = lambda size: ImageFont.truetype(str(font_path), size)
image = Image.new('RGB', (1600, 1510), '#f5f6f8')
draw = ImageDraw.Draw(image)
navy = '#182636'
muted = '#516274'
blue = '#173c6b'

def text(x, y, value, size=27, color=navy):
    draw.text((x, y), str(value), fill=color, font=font(size))

def wrapped(value, x, y, max_width, size=25, color=muted, step=39):
    current = ''
    for char in value:
        if char == '\n' or draw.textlength(current + char, font=font(size)) > max_width:
            text(x, y, current, size, color)
            y += step
            current = '' if char == '\n' else char
        else:
            current += char
    if current:
        text(x, y, current, size, color)
        y += step
    return y

draw.rectangle((0, 0, 1600, 16), fill=blue)
text(72, 54, '节点执行与撤销 · 接口结果摘录', 42)
text(72, 118, '数据报告，非浏览器截图；不是产品界面或真实模型效果展示', 28, '#a44319')
text(72, 168, '来源：实际独立 FastAPI / Uvicorn 进程重启 JSON · 2026-10-27', 25, muted)

picture = next(n for n in data['canvas']['nodes'] if n['node_id'] == 'picture')
execution = picture['last_execution']
rows = [
    ('自动测试', f'{unit} 项单测 + {retained} 项保留回归 = {unit + retained} 项通过'),
    ('重启后的执行状态', execution['status'].upper() + ' — 已入队，不代表生成完成'),
    ('媒体任务总数', str(data['task_count']) + ' — 幂等重放没有新增任务'),
    ('重启场景运行 worker', str(data['worker_executed']).lower() + ' — 明确禁用'),
    ('真实模型调用', str(data['real_model_calls'])),
    ('幂等回放', str(data['idempotent_replay']).lower()),
    ('重启后，撤销前节点', ', '.join(data['nodes_after_restart_before_undo'])),
    ('撤销后节点', ', '.join(data['restored_nodes'])),
    ('恢复视口', json.dumps(data['canvas']['viewport'], ensure_ascii=False)),
    ('恢复图片节点位置', json.dumps(picture['position'], ensure_ascii=False)),
    ('恢复连线', 'words → picture'),
]
y = 230
for index, (key, value) in enumerate(rows):
    draw.rectangle((72, y, 1528, y + 66), fill='#ffffff' if index % 2 == 0 else '#e9eef4')
    text(92, y + 16, key, 25, muted)
    text(480, y + 16, value, 26)
    y += 66

y += 34
text(72, y, '可核对的原始标识', 28, blue)
y += 47
for key in ['project_id', 'task_id', 'execution_id', 'operation_id']:
    text(72, y, f'{key}: {data[key]}', 22, muted)
    y += 37
y += 16
y = wrapped('边界：此重启检查关闭后台 worker，仅验证排队任务、删除记录与撤销恢复。另一个单测手动执行实际 worker，供应商返回固定 32×24 PNG；不是外部模型实测。新前端按钮正在接入，多角色 Agent 媒体工具仍未接通。', 72, y, 1450, size=24, step=36)
y += 15
text(72, y, 'JSON SHA-256', 21, muted)
y += 31
text(72, y, hashlib.sha256(SOURCE.read_bytes()).hexdigest(), 21, muted)
image.save(BASE / '接口结果摘录.png', optimize=True)
print('Rendered JSON report: 接口结果摘录.png (not a browser screenshot)')
