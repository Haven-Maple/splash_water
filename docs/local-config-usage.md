# 本地配置文件使用说明

## 1. 目的

为了避免每次启动后端都手动设置环境变量，后端现已支持优先读取本地配置文件。

读取优先级如下：

1. 环境变量
2. `backend/local_config.json`
3. 代码内默认值

这意味着：

- 日常本机开发建议使用本地配置文件
- 如有特殊需要，仍可用环境变量覆盖

## 2. 如何创建配置文件

先复制示例文件：

```text
backend/local_config.example.json
```

复制为：

```text
backend/local_config.json
```

然后填入你的真实配置，例如：

```json
{
  "access_key": "1980595211412008960",
  "secret_access_key": "你的真实密钥",
  "product_id": "1652584849",
  "api_version": "v1",
  "domain_name": "open.cloud-dahua.com",
  "accept_language": "zh-CN",
  "request_timeout_seconds": 20,
  "frontend_origins": [
    "http://localhost:5173",
    "http://127.0.0.1:5173"
  ],
  "data_root": "data"
}
```

## 3. 如何启动后端

在项目根目录下：

```cmd
cd /d C:\Users\Maple_Rain\Documents\Items\splash_water
.venv\Scripts\activate.bat
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

如果你已经在 `backend` 目录，也可以直接：

```cmd
..\.venv\Scripts\python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 4. 如何检查配置是否生效

启动后访问：

```text
http://127.0.0.1:8000/api/health
```

如果返回中：

- `ok` 为 `true`
- `dahuaConfigured` 为 `true`

说明配置已被后端正确读取。

## 5. 安全说明

真实配置文件：

```text
backend/local_config.json
```

已加入 `.gitignore`，默认不会进入版本控制。

请不要把真实密钥写入：

- 示例文件
- 文档
- 提交记录
