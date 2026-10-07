# 矿安智识后端

矿井人员识别与安全管理 API 已实现，服务基地址为 `http://<运行机 IP>:5000/api`。

```powershell
python -m pip install -r backend/requirements.txt
python database/init_db.py
python -m uvicorn backend.main:app --host 0.0.0.0 --port 5000
```

启动后，接口文档在 `http://127.0.0.1:5000/docs`，前后端字段约定见 `docs/api-contract.md`。

## 账号与权限

账号保存在 SQLite 的 `users` 表中，不再直接写死在登录代码里。初始化数据库时会创建以下演示账号：

| 用户名 | 初始密码 | 角色 | 关联人员 |
| --- | --- | --- | --- |
| `admin` | `123456` | 系统管理员 | 无 |
| `guard` | `123456` | 井口安检员 | 无 |
| `miner` | `123456` | 井下作业人员 | P003 |

角色权限如下：

- `admin`：全部功能，并可以创建、查看、启用或禁用账号。
- `guard`：系统总览、识别中心、出入井记录、告警管理。
- `miner`：只能查看自己关联人员的基础信息和出入井记录。

一个矿场可以创建多个管理员、多个安检员和多个矿工账号。矿工账号必须关联一个人员档案；管理员和安检员不需要关联人员。正式部署后应立即修改演示账号密码。

除登录和健康检查外，受保护接口需要携带：

```text
Authorization: Bearer demo-<用户名>-token
```

当前 token 仍是联调用的演示格式，正式生产环境应替换为真正的 JWT 或服务端会话。

安装 `insightface onnxruntime ultralytics` 后，人脸和视频接口会自动启用真实模型。基础依赖下，照片注册提供仅用于流程联调的文件特征比对；视频会保存上传文件，并明确提示尚未分析。

云端联调地址：`http://120.26.89.44:5000`。云服务器安全组和系统防火墙需要允许 TCP 5000 入站；正式部署建议改用 HTTPS 域名，并将前端 API 地址改为 HTTPS 地址。

部署新版时，后端代码、`database/schema.sql`、`database/init_db.py` 和前端页面必须一起更新。数据库初始化函数会自动创建 `users` 表；已有数据库不会被删除。
