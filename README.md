# 三方 JSON 合并 API

面向配置审阅的三方(3-way)JSON 合并服务。提交 `base`、`left`、`right` 三份 JSON 及可选的数组身份字段映射,服务按 JSON Pointer(RFC 6901)递归合并,返回合并结果与冲突清单。

## 一键启动

```bash
docker compose up --build
```

服务地址:**http://localhost:8000**

- 交互式接口文档(Swagger UI):http://localhost:8000/docs
- 健康检查:http://localhost:8000/health

## 配置

通过环境变量配置(可在 `docker-compose.yml` 的 `environment` 中修改):

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_REQUEST_BYTES` | `5242880`(5 MiB) | 单个请求体大小上限,超限返回 `413` |

服务监听端口为容器内 `8000`,Compose 默认映射到宿主机 `8000`,可在 `docker-compose.yml` 的 `ports` 中调整。

## API

### `GET /health`

健康检查,返回 `{"status": "ok"}`。

### `POST /merge`

请求体:

```json
{
  "base":  { "replicas": 1, "servers": [ { "name": "a", "weight": 1 } ] },
  "left":  { "replicas": 2, "servers": [ { "name": "a", "weight": 5 } ] },
  "right": { "replicas": 1, "servers": [ { "name": "a", "weight": 1 }, { "name": "b", "weight": 3 } ] },
  "array_identity": { "/servers": "name" }
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `base` | 任意 JSON | 是 | 共同祖先版本 |
| `left` | 任意 JSON | 是 | 一侧变更 |
| `right` | 任意 JSON | 是 | 另一侧变更 |
| `array_identity` | `object<string, string>` | 否 | 数组身份字段映射:键为对象数组的 JSON Pointer,值为数组项的身份字段名 |

响应体(`200`):

```json
{
  "merged": {
    "replicas": 2,
    "servers": [ { "name": "a", "weight": 5 }, { "name": "b", "weight": 3 } ]
  },
  "conflicts": []
}
```

- `merged`:合并结果。
- `conflicts`:冲突列表,按 `pointer` 字典序排序;每个冲突包含:
  - `pointer`:冲突位置的 JSON Pointer(根为 `""`);
  - `kind`:`delete_vs_edit`(一侧删除、另一侧修改)或 `edit_vs_edit`(双侧改为不同值);
  - `base` / `left` / `right`:三方各自的值,某一侧不存在该值时为 `null`。

相同输入必然产生相同输出(确定性)。

### 示例

```bash
curl -s http://localhost:8000/merge \
  -H 'Content-Type: application/json' \
  -d '{
        "base":  {"a": 1},
        "left":  {"a": 2},
        "right": {"a": 3}
      }'
```

```json
{
  "merged": {"a": 1},
  "conflicts": [
    {"pointer": "/a", "kind": "edit_vs_edit", "base": 1, "left": 2, "right": 3}
  ]
}
```

## 合并规则

以 `base` 为基准,对每个位置(按 JSON Pointer 定位)递归比较三方:

1. **仅单侧变化**(另一侧与 base 相同)→ 自动采纳变化侧;
2. **双侧相同变化**(含双侧都删除)→ 采纳;
3. **一侧删除、另一侧修改** → 保留 base,记录 `delete_vs_edit` 冲突;
4. **双侧改为不同的值** → 保留 base,记录 `edit_vs_edit` 冲突;
5. 三方均为对象且互不相同 → 按键递归合并;
6. **指定了身份字段的对象数组**(`array_identity`)→ 按身份合并增删改:
   - base 中既有项保持 base 的顺序逐项三方合并(项内字段递归);
   - 仅出现在 left/right 的新增项,按身份值排序后追加到末尾;
   - 数组项的冲突指针以身份值作为路径段,如 `/servers/a/weight`;
   - 若任一侧数组中存在缺少身份字段或身份重复的项,该数组回退为整体值语义;
7. **未指定身份字段的数组** → 视为整体值:仅单侧变化则采纳,双侧异值变化则整体冲突。

确定性保证:对象键按 base 顺序、其余键按字典序输出;新增数组项按身份排序;冲突按 `pointer` 排序。

## 本地开发

```bash
pip install -r requirements-dev.txt
pytest                 # 运行测试
uvicorn app.main:app --reload --port 8000
```
