# 换钥演练测试记录

日期：2026-11-21 · 环境：Linux 容器（无 Docker，未起 MySQL/Redis）

## 自动化

```bash
cd backend
python -m pytest tests -q
# 18 passed
```

- `tests/test_rekey_service.py`：服务层 11 例——新写即新钥；中断后续改且重放幂等；并发新写优先（superseded、不改页）；退役后旧页对外读受阻、恢复通道可读；旧钥不保留时恢复也受阻；切主在未全覆盖时阻断；全覆盖后允许；west 可接班而 east 被排除；无覆盖者时阻断；退役后禁止再改页。
- `tests/test_rekey_api.py`：HTTP 层 7 例——观察员只读（开始/写页 403、读 200）；新写从主库立即可读且 east 给出受阻原因；中断→重试→再重试（`already_applied`、0 改页）→退役→旧页受阻/恢复可读→推进 409；切主先阻断后通过；仅管理员可退役与登记密钥；同一旧钥不能并发两场换钥。

## UI 端到端（jsdom，无浏览器）

容器缺少 Chromium 运行库且无 root，采用 jsdom + esbuild 打包真实前端模块，经本地代理连到真实 FastAPI/SQLite 后端。脚本：`/tmp/e2e/flow.mjs`。覆盖：登录 → 换钥页渲染五区块 → 切换到已退役演练 → west 读旧页受阻并显示原因 → 恢复通道可读 → 推进按钮禁用 → 新建演练 → 推进 tick 增长 → 中断/重试批次状态 → 表单写页落库且 key_id 为新钥 → 切主结论与“新钥全覆盖”证据渲染。结果：23 项断言全过，无 JS 运行时错误。

## 手工 API 核查

- 中断批次得 2 done / 2 pending，重试改 2 页，再次重试 `changed_pages=0`。
- 退役后旧页读返回“换钥已到期…”，恢复通道返回“故障恢复：旧钥保留…”。
- 退役且仍有 12 旧页时切主：`新钥全覆盖 pass=False`，决策 blocked。
- 原选主模块对旧输入的 SHA256 与此前一致（hash match True），改动不破坏既有演练。

## 已知差异

- 换钥为本地表内模拟：页面值是明文字符串，密钥不落真实 KMS，读写不涉及真实加解密。
- 备库自动追赶为每次推进 1 页；“备库追平”按钮一次补满，便于观察门禁变化。
- 未在 Docker Compose 五服务下复跑（环境无 Docker）；使用 SQLite 与真实 uvicorn 验证。
- 未做视觉截图比对：环境无 Chromium；DOM 快照见 `/tmp/e2e/rekey-dom.html`。
