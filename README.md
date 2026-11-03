# Token 账本（astrbot_plugin_token_stats）

在 QQ 里用一条命令，查这台 AstrBot 每天到底烧了多少 token、缓存命中多少。

- 插件名：`astrbot_plugin_token_stats`
- 版本：`1.0.0`
- 作者：RenderRD
- 适用：AstrBot `>= 4.17.0`（在 4.29.3 上实测）
- 依赖：无额外依赖，只用 Python 标准库 `sqlite3`
- 数据来源：AstrBot 自己的 `data_v4.db` → `provider_stats` 表，**只读**，不参与任何请求链路

---

## 1. 命令速查

| 命令 | 说明 |
|---|---|
| `/token` | 今天汇总（次数 / 命中 / 未命中 / 输出），附本月累计对照 |
| `/token 昨天` | 昨天汇总 |
| `/token 7` | 最近 7 天逐日（`1`–`90` 任意整数） |
| `/token 30` | 最近 30 天逐日 |
| `/token 明细` | 今天按小时 |
| `/token 会话` | 今天按群 / 私聊拆分 |
| `/token 全部` | 历史每一天 |
| `/token帮助` | 用法说明 |

群聊、私聊都能用；不限权限时所有人可发（可用配置 `allowed_users` 收紧）。

## 2. 输出长什么样

今天汇总：

```
📊 Token 账本 · 11-03
调用 33 次 · 合计 1,059,483
├ 输入合计 1,056,640
│   ├ 命中 515,072（49%）
│   └ 未命中 541,568
└ 输出 2,843
── 本月对照 ──
本月 4,401,092 · 121 次 · 命中 53%
```

按小时（`/token 明细`）：

```
📊 今日按小时 · 11-03
00时  2次  miss 48,550 / hit 21,248
01时  3次  miss 60,205 / hit 42,496
08时  20次  miss 250,683 / hit 363,264
```

按会话（`/token 会话`）：

```
📊 今日按会话 · 11-03
群 9999999  33次  miss 541,568 / hit 515,072
私聊 9999999  2次  miss 22,267 / hit 18,944
```

逐日（`/token 7`）：

```
📊 Token 账本 · 最近 7 天
10-28  8,220,720  326次  命中63%
10-29  5,536,946  225次  命中68%
...
────────────
合计 23,166,446 · 864 次 · 命中 61%
```

## 3. 数据从哪来

`data_v4.db` 的 `provider_stats` 表，AstrBot 每完成一次 LLM 调用写一行：

| 列 | 含义 | 插件怎么用 |
|---|---|---|
| `created_at` | 调用时间，**UTC 无时区** | 换算成北京时间后按本地日界切天 |
| `umo` | `平台ID:GroupMessage:群号` / `平台ID:FriendMessage:QQ号` | `/token 会话` 的拆分依据 |
| `conversation_id` | 会话 ID | 不使用（约 55% 的行为空） |
| `provider_id` / `provider_model` | 用的哪个模型 | 不拆分（当前只有 `deepseek-v4.1-flash-1017` 在跑） |
| `token_input_other` | 输入里**未命中缓存**的部分 | 报表里的「未命中」 |
| `token_input_cached` | 输入里**命中缓存**的部分 | 报表里的「命中」 |
| `token_output` | 输出 token | 报表里的「输出」 |
| `status` | 目前全是 `completed` | 不做过滤 |
| `agent_type` | 目前全是 `internal` | 不做过滤 |
| `start_time` / `end_time` / `time_to_first_token` | 性能字段 | 未使用 |

要记住的两点：

1. **只统计 LLM 对话调用**。嵌入（embedding）走的是另一条 provider 管路，不写这张表，所以 LivingMemory 的向量调用不在账里。
2. **决策 AI（读空气）也算一次调用**。它用的同一个模型，所以账里既有"真回话"，也有"判断要不要回话"。

## 4. 统计口径

- **时区**：数据库存 UTC，插件固定按 **UTC+8（北京时间）** 切日界。所以 `/token` 的「今天」= 北京 00:00–24:00。
- **命中率** = `命中 / (命中 + 未命中)`，只看输入，不含输出。
- **合计** = 命中 + 未命中 + 输出（官方账单口径也是三者相加）。
- **本月对照**：当月 1 号 00:00 到明天 00:00（含今天）。
- **区间报表**：`/token N` 从 N-1 天前到今天，逐日列出；没有调用的日子会跳过。
- **按会话**：按 `umo` 分组，显示成「群 191540448」「私聊 2070097886」。旧平台的 `鱼:...` 记录会显示成同一个群号，属于历史遗留。

## 5. 安装 / 更新 / 卸载

安装（本机 → 服务器）：

```bash
scp -i <pem> -r astrbot_plugin_token_stats root@<服务器>:/opt/qqbot/astrbot/data/plugins/
docker compose -f /opt/qqbot/docker-compose.yml restart astrbot
```

校验语法（容器内路径）：

```bash
docker exec astrbot python3 -m py_compile /AstrBot/data/plugins/astrbot_plugin_token_stats/main.py
```

更新：覆盖 `main.py` 等文件后重启容器（或用面板「插件」页重载）。

卸载：删掉目录 + 重启容器即可，数据库一个字节都不会被动过。

## 6. 配置

`_conf_schema.json` 只有一个可选项：

```json
{
  "allowed_users": {
    "description": "允许使用 /token 的 QQ 号；留空 = 所有人可用",
    "type": "list",
    "default": []
  }
}
```

填了之后，不在名单里的发送者会收到「这个命令只对指定用户开放。」。

## 7. 常见问题

**Q：为什么和 DeepSeek 后台的账单对不上？**
A：这里统计的是 AstrBot 记录到的 token 数（含命中缓存的输入），官方账单还会折算价格、命中价与未命中价不同。数量级应当一致，金额不能直接换算——命中缓存的价格通常是未命中的 1/10 左右。

**Q：命中率为什么忽高忽低？**
A：命中依赖「前缀完全一致」。三种情况会整段重付：
- 容器重启后的第一条（缓存全丢，等于全价重来）；
- 上下文前缀被改写（历史被截断/重排）；
- 换了模型或改了系统提示词/人设。

**Q：为什么有时候一天几十万 token，有时候只有几千？**
A：看调用次数和每次携带的历史长度。群聊活跃度高、上下文长，单次输入就大；只判断不回话的「决策调用」也会计入。

**Q：能看每个模型分别花了多少吗？**
A：当前版本合并统计。想拆的话把 `build_day_report` 里的 `_query` 改成按 `provider_model` 分组即可。

**Q：数据会过期吗？**
A：不会，插件只读，`provider_stats` 由 AstrBot 自己维护；删了会话历史也不影响已记录的用量。

## 8. 版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0.0 | 2026-11-03 | 首个版本：今日/昨日/区间/按小时/按会话/历史，全部按北京时间日界 |

## 9. 开发者备注

- 报表函数都是纯函数，签名统一为 `build_*_report(db: Path, ...) -> str`，方便单独测试：

```python
import importlib.util, sys
sys.path.insert(0, "/AstrBot")
spec = importlib.util.spec_from_file_location("tok", "/AstrBot/data/plugins/astrbot_plugin_token_stats/main.py")
m = importlib.util.module_from_spec(spec); sys.modules["tok"] = m; spec.loader.exec_module(m)
print(m.build_day_report(m._db_path(), 0))
```

- 想加「按价格折算」：在报表末尾按 `miss * 单价_in + cached * 单价_cached + out * 单价_out` 拼一行即可，注意单价会变，建议放配置项而不是写死。
- 数据库连接一律 `mode=ro`，不要在插件里写库——AstrBot 在跑，写库有锁风险。
