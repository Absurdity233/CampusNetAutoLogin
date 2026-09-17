# Windows 校园网自动登录

连接校园 Wi-Fi 后，程序用 Microsoft Edge 打开认证页面，自动填写账号密码并提交；网络断开时会自动重试。密码只保存在当前 Windows 用户的凭据管理器中，不写入配置文件。

程序启动后会等待 Wi-Fi 最长 30 秒，避免刚开机或刚连接热点时误判为未连接。默认只认证当前已连接的校园 Wi-Fi，不会擅自切换热点。

## 安装

1. 双击 `install.bat`。
2. 输入校园网登录 URL；只输入 IP 或域名时会自动补充 `http://`，不知道时可留空自动跳转。
3. 输入校园 Wi-Fi 名称、账号和密码。程序只会在指定 Wi-Fi 上提交凭据。
4. 连接校园 Wi-Fi，双击 `test-login.bat` 测试。

安装脚本会创建独立 Python 环境，并写入当前用户的 Windows 开机启动项。开机后程序在后台运行，不显示窗口。

## 常用操作

- `configure.bat`：重新设置网址、Wi-Fi、账号和密码。
- `test-login.bat`：显示 Edge 并执行一次登录，适合排错。
- `status.bat`：查看 Wi-Fi、联网、凭据和开机启动状态。
- `uninstall.bat`：移除开机启动和已保存凭据，但保留项目文件。

## 页面元素无法识别

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

## 注意事项

- 仅用于你本人有权登录的校园网账号。
- 如果页面有验证码、短信验证、扫码或动态口令，无法完全无人值守登录。
- 默认严格校验 HTTPS 证书；不要为不可信认证页面关闭证书校验。
- 学校修改认证页面后，可能需要更新 CSS 选择器。

## 参考项目

本项目的校园网自动认证思路参考了 [ArousX/CampusNetAutoLogin](https://github.com/ArousX/CampusNetAutoLogin)。本项目采用 Playwright 驱动 Edge 完成网页表单登录，并增加了 Wi-Fi 限制、断线重试和 Windows 凭据管理器存储。
