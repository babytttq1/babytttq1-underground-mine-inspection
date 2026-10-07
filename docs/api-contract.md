# 前后端接口契约

基地址：`http://<服务地址>:5000/api`。除文件上传外，正文与响应均为 JSON；文件字段统一名为 `file`；时间格式为 `YYYY-MM-DD HH:mm:ss`；失败响应统一为 `{"error":"说明"}`。

## 认证与角色

`POST /auth/login` 和 `GET /health` 不需要认证。其他接口需要请求头：

```text
Authorization: Bearer demo-<用户名>-token
```

角色：

- `admin`：全部接口，包括账号管理。
- `guard`：人员查询、识别、进出井记录、告警和摄像头查询。
- `miner`：只能查询自己关联人员的信息、自己的进出井记录和个人轨迹。

越权访问返回 `403`，未登录或账号已禁用返回 `401`。

| 接口 | 请求关键字段 | 成功响应关键字段 |
| --- | --- | --- |
| `POST /auth/login` | `username`, `password` | `token`, `role`, `name` |
| `GET /users` | 管理员权限 | 用户账号数组 |
| `POST /users` | `username`, `password`, `role`；矿工必须提供 `person_id` | `user_id`, `username`, `role`, `person_id` |
| `PUT /users/{user_id}/status` | `status`：`active` 或 `disabled` | `user_id`, `status` |
| `POST /persons` | `name`, `department`, `status` | `person_id`, `created_at` |
| `GET /persons` | 可选 `status` | 人员数组 |
| `GET /persons/{person_id}` | 路径人员编号 | 人员详情 |
| `POST /persons/{person_id}/face` | `multipart/form-data`，`file` | `person_id`, `feature_extracted` |
| `POST /face/recognize` | `multipart/form-data`，`file`；可选 `camera_id` | `person_id`, `name`, `confidence`, `is_known` |
| `POST /records/in-out` | `person_id`, `record_type`, `camera_id` | `record_id`, `record_time`, `status` |
| `GET /records/in-out` | 可选 `person_id` | 进出井记录数组 |
| `POST /videos` | `multipart/form-data`，`file`；可选 `camera_id` | `recognition_count`, `results` |
| `GET /tracks/{person_id}` | 路径人员编号 | 轨迹数组 |
| `GET /alerts` | 无 | 告警数组 |
| `PUT /alerts/{alert_id}` | `status`；可选 `handler` | `alert_id`, `status` |
| `GET /cameras` | 无 | 摄像头数组 |

前端显示约定：`is_known=1` 才显示人员姓名；`is_known=0` 显示“未知人员”。视频结果中的 `bbox` 为 `[x1, y1, x2, y2]`，`frame_number` 为视频帧序号，`track_id` 用于同一视频内的人员跟踪。提交 `POST /records/in-out` 后以后端返回的 `status` 刷新人员状态。

账号创建规则：用户名必须唯一，密码至少 6 位；`role=miner` 时必须传入已存在的 `person_id`，且一个人员只能关联一个账号。管理员和安检员的 `person_id` 可以为空。
