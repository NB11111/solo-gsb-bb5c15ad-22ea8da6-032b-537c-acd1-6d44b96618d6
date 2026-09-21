# Three-way JSON Merge API

面向**配置审阅**场景的三方 JSON 合并服务（Python 3.12 + FastAPI）。提交一份公共
基线 `base` 和两个修订版 `left` / `right`，服务沿 [JSON Pointer (RFC 6901)](https://www.rfc-editor.org/rfc/rfc6901)
递归合并，并给出确定的结果与冲突清单。

## 一条命令启动

```bash
docker compose up --build -d
```

服务在宿主机 **8000** 端口启动（可用环境变量 `PUBLISH_PORT` 改映射端口）：

- 健康检查：<http://localhost:8000/health>
- 合并接口：`POST http://localhost:8000/merge`
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI Schema：<http://localhost:8000/openapi.json>

停止：`docker compose down`；查看日志：`docker compose logs -f`。

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_REQUEST_SIZE_BYTES` | `10M` | 请求体大小上限。纯字节数（如 `1048576`），或带 `K` / `M` / `G`（1024 进制）后缀，如 `256K`、`10M`、`1G`。`0` 表示不限制。超限返回 `413`。 |
| `PUBLISH_PORT` | `8000` | 仅 Compose 使用：宿主机映射端口（容器内固定监听 8000）。 |

可以直接在环境里设置，或在仓库根目录放一份 `.env`（`docker compose` 会自动读取）：

```dotenv
MAX_REQUEST_SIZE_BYTES=10M
PUBLISH_PORT=8000
```

## 合并语义

沿 JSON Pointer 递归，对每个位置比较 `base` / `left` / `right`：

| 情况 | 结果 |
| --- | --- |
| 只有一侧改动（另一侧与 base 相同） | 自动采纳改动 |
| 两侧做出相同改动 | 合并该改动（只生效一次） |
| 两侧都删除同一字段/项 | 删除 |
| 一侧删除、另一侧修改（或一侧缺失时两侧新增不同值） | **保留 base**，报告冲突 |
| 两侧把同一值改成不同结果（叶子值/普通数组） | **保留 base**，报告冲突 |
| 两侧修改同一对象的不同字段 | 各自自动合并；仅冲突字段报错 |
| 未配置身份字段的数组 | 视为**整体值**（整体替换 / 整体冲突，不逐项 diff） |

冲突对象的 `kind`：

- `both_modified`：两侧把同一值改成不同结果（或 `null` 与其他值互斥修改）；
- `delete_vs_modify`：一侧删除、另一侧修改；
- `both_added`：base 中不存在该路径，两侧新增了不同的值（含身份数组中同 ID 新增内容不同）。

冲突一律保留 base 值，冲突记录包含五个字段：

```json
{ "pointer": "/servers/0/port", "kind": "both_modified",
  "base": 8080, "left": 9090, "right": 80 }
```

被删除的一侧在冲突里显示为 `null`。冲突按 `pointer`（同指针再按 `kind`）升序排列；
相同输入输出完全确定。

### 对象数组与身份字段

在 `arrayIdentity` 中声明“数组指针 → 身份字段名”：

- 数组按身份字段识别同一项，进行**增 / 删 / 改**合并；
- 既有项**保持 base 中的顺序**；一侧删除而另一侧修改时保留 base 项并报告
  `delete_vs_modify`，冲突指针指向该项的最终下标（如 `/srv/1`）；
- 新增项（单侧新增，或两侧同 ID 同内容）按身份值排序后追加，排序次序：
  `null < 布尔 < 数字（按数值） < 字符串（按字典序）`；
- 指针段 `*` 通配单个位置下标，可用于嵌套数组，例如
  `"/servers/*/disks": "name"`；
- 任一侧出现非对象元素、缺少身份字段、或身份重复时，该数组自动退化为整体值比较。

## 请求与响应

`POST /merge`，字段 `base` / `left` / `right` 为任意 JSON 文档；
`arrayIdentity`（也接受别名 `array_identity`）为“指针 → 字段名”映射，可省略。

请求：

```bash
curl -s -X POST http://localhost:8000/merge \
  -H 'Content-Type: application/json' \
  -d '{
    "base":  {"servers": [{"id": "a", "port": 8080}, {"id": "b", "port": 80}]},
    "left":  {"servers": [{"id": "a", "port": 9090}, {"id": "b", "port": 80}]},
    "right": {"servers": [{"id": "a", "port": 8080}, {"id": "b", "port": 81}]},
    "arrayIdentity": {"/servers": "id"}
  }'
```

响应：

```json
{
  "merged": {
    "servers": [
      {"id": "a", "port": 9090},
      {"id": "b", "port": 81}
    ]
  },
  "conflicts": []
}
```

冲突示例（a 被 left 删除、被 right 修改）：

```json
{
  "merged": {"servers": [{"id": "a", "port": 8080}, {"id": "b", "port": 80}]},
  "conflicts": [
    {
      "pointer": "/servers/0",
      "kind": "delete_vs_modify",
      "base": {"id": "a", "port": 8080},
      "left": null,
      "right": {"id": "a", "port": 9090}
    }
  ]
}
```

指针遵循 RFC 6901 转义：`~` → `~0`，`/` → `~1`；根文档冲突的指针为空字符串 `""`。

## 本地开发（不使用 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
pytest -q
```

## 项目结构

```
app/
  main.py         FastAPI 路由（/health、/merge）
  merger.py       三方合并引擎（JSON Pointer、身份数组、冲突建模）
  middleware.py   请求体大小上限 ASGI 中间件
  config.py       环境变量配置与大小解析
  models.py       请求/响应 Pydantic 模型
tests/            引擎与 HTTP 层测试（pytest）
Dockerfile        精简镜像（python:3.12-slim，非 root，内置 HEALTHCHECK）
docker-compose.yml 一键启动
```
