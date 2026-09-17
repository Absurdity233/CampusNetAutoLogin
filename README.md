# Windows 校园网自动登录

连接校园 Wi-Fi 后，程序用 Microsoft Edge 打开认证页面，自动填写账号密码并提交；网络断开时会自动重试。密码只保存在当前 Windows 用户的凭据管理器中，不写入配置文件。

程序使用状态机区分等待 Wi-Fi、错误网络、认证中、认证失败和已联网等状态。重复失败会指数退避；Wi-Fi 发生变化时会提前结束等待并重新检测。默认只认证当前已连接的校园 Wi-Fi，不会擅自切换热点。

## 安装

1. 双击 `install.bat`。
2. 输入校园网登录 URL；只输入 IP 或域名时会自动补充 `http://`，不知道时可留空自动跳转。
3. 输入校园 Wi-Fi 名称、账号和密码。程序只会在指定 Wi-Fi 上提交凭据。
4. 连接校园 Wi-Fi，双击 `test-login.bat` 测试。

安装脚本会创建独立 Python 环境，并创建当前用户的 Windows 登录计划任务。用户登录 10 秒后程序在后台运行，不显示窗口；重新安装会自动移除旧版本使用的注册表启动项。

## 常用操作

- `configure.bat`：重新设置网址、Wi-Fi、账号和密码。
- `test-login.bat`：显示 Edge 并执行一次登录，适合排错。
- `status.bat`：查看 Wi-Fi、联网、凭据和计划任务状态。
- `uninstall.bat`：移除计划任务和已保存凭据，但保留项目文件。

## Portal 适配器

默认使用 `auto` 模式，按以下顺序选择认证页面适配器：

1. `configured`：配置文件中填写了自定义 CSS 选择器；
2. `eportal`：识别常见的 `#username`、`#pwd` 和 `#loginLink` 页面；
3. `generic`：通过输入框类型和按钮文本进行通用识别。

可在配置文件中强制指定适配器：

```json
{
  "portal_adapter": "eportal"
}
```

### 页面元素无法识别

多数认证页无需额外设置。如果日志提示找不到输入框或登录按钮，运行：

```bat
.venv\Scripts\python.exe campusnet.py open-config
```

在配置文件中填写 CSS 选择器：

```json
{
  "username_selector": "#username",
  "password_selector": "#password",
  "submit_selector": "#login-button",
  "agreement_selector": "#agree"
}
```

只修改对应字段，保存后再次运行 `test-login.bat`。`agreement_selector` 仅在页面要求勾选用户协议时设置。

## 文件位置

- 配置：`%LOCALAPPDATA%\CampusNetAutoLogin\config.json`
- 日志：`%LOCALAPPDATA%\CampusNetAutoLogin\campusnet.log`
- 密码：Windows 凭据管理器中的 `CampusNetAutoLogin/default`

如需让程序主动切换到已保存的校园 Wi-Fi，在配置文件中设置：

```json
{
  "auto_connect_wifi": true
}
```

## 重试策略

相同错误会按指数退避延长重试间隔，错误类型或 Wi-Fi 变化后会从初始间隔重新开始。可以在高级配置中调整：

```json
{
  "retry_initial_seconds": 5,
  "retry_max_seconds": 300,
  "retry_multiplier": 2.0,
  "retry_jitter_ratio": 0.15,
  "retry_poll_seconds": 2.0
}
```

`retry_jitter_ratio` 会为重试时间加入少量随机抖动；`retry_poll_seconds` 控制等待期间检测 Wi-Fi 变化的频率。

## 注意事项

- 仅用于你本人有权登录的校园网账号。
- 如果页面有验证码、短信验证、扫码或动态口令，无法完全无人值守登录。
- 默认严格校验 HTTPS 证书；不要为不可信认证页面关闭证书校验。
- 学校修改认证页面后，可能需要更新 CSS 选择器。

## 参考项目

本项目的校园网自动认证思路参考了 [ArousX/CampusNetAutoLogin](https://github.com/ArousX/CampusNetAutoLogin)。本项目采用 Playwright 驱动 Edge 完成网页表单登录，并增加了 Wi-Fi 限制、断线重试和 Windows 凭据管理器存储。
