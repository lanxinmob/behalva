# WSL2 部署与 Windows 迁移

核心、SQLite、任务调度、索引和模型请求运行在 WSL2；代码仓库可留在 D 盘。Linux 构建、运行版本和数据库放在发行版自己的文件系统，不让 Windows 与 Linux 同时打开同一个 SQLite 文件。

Windows 专用浏览器可继续使用原工作区的浏览器配置、worker.token 和 server.pem，通过 HTTPS 连接 Linux 核心。浏览器的 Windows 登录状态不能靠复制到 Linux 保证兼容，因此迁移工具保留原浏览器目录，由 Windows 浏览器适配器继续管理。

## 准备 Linux 环境

在已启用 systemd 的 Ubuntu 中安装 `cmake g++ libssl-dev libsqlite3-dev python3-venv`，创建虚拟环境并安装 `requirements.txt`。使用 CMake 在 Linux 本地目录构建。正式运行的 release 应从已通过双审查的 Git 提交导出，带 `build/assistant` 和 `.venv/bin/python`，不要直接运行随时修改的开发目录。

## 迁移现有工作区

1. 停止对应 Windows 核心、调度器和浏览器适配器，核对正在执行或结果不明的任务。保留完整旧目录与旧运行版本；不要删除 WAL 文件。
2. 在原 Windows 账号运行 `tools/migrate_workspace.py`，指定 `--source`、`--target`、`--distro` 和 `--linux-python`。目标必须是全新的 Linux 本地目录。工具不启动后台服务。
3. 工具持有源实例锁，从停止后的数据库及 WAL 制作一致性副本；API Key 在 Windows 内存解密，经本机匿名管道交给 Linux，使用仅当前用户可读的主密钥重新加密。不打印密钥或将明文密钥写入交接文件。
4. 原令牌、TLS 证书、对话、规则和对象保留；当前文件路径及目录授权转换为 `/mnt/<盘符>/...`。资料及授权 ID 保持稳定，历史引用保留原始记录。源文件不改动。包超过 512 MiB、源正在运行、结果不明或目标已存在时停止，不覆盖。
5. 核对数据数量、完整性、凭据可解密、旧引用及撤权行为。真实模型连通性验证应单独声明是否执行，不能把本地解密成功称作 API 调用成功。

若 Windows 账户已无法解密旧凭据，默认迁移停止，原目录保持不变。确实需要先迁移其他资料时可显式加 `--allow-unavailable-credentials`：报告列出不可恢复的供应商 ID，保留旧密文，模型标记为不可用；在电脑的“连接与授权”编辑该供应商重新填写 API Key 后恢复。只改名称或留空保存不会清除此状态。不能宣称这种情况已迁移成功全部密钥。

## 后台启动

在 Linux 运行已冻结版本的 `tools/wsl_service.py install --release <release> --data <workspace> --port <port>`，创建当前 Linux 用户的 `personal-assistant.service`。安装本身不启动或启用服务。服务使用 `UMask=0077`，整个进程组随服务停止，核心失败后不自动重启。

用户批准启用后，在 Windows 用冻结版本的 `tools/start-wsl.ps1 -Release <Linux release> -BrowserData <原 Windows 工作区> -Port <port>` 启动。该脚本持有一个独立 WSL 前台进程，保持虚拟机运行，并管理 Windows 浏览器适配器；与聊天窗口、WSL 终端标签页无关。

需要 Windows 登录后自动启动时，运行 `tools/install-wsl-autostart.ps1` 并传入相同参数。它创建当前 Windows 用户的低权限登录任务，不保存密码，不立即启动。任务名为 `Personal Assistant WSL2 - personal-assistant`。需要启动当前会话时使用计划任务的“运行”。这提供的是登录后运行；关机、休眠或尚未登录时不可用，手机仍依赖离线缓存。

查看状态：`wsl -d <发行版> -- systemctl --user status personal-assistant`。

停止：`wsl -d <发行版> -- systemctl --user stop personal-assistant`。保持进程会退出并停止 Windows 浏览器适配器。要停用登录启动，禁用对应 Windows 计划任务。

Windows 浏览器仍访问同一 `https://127.0.0.1:<port>`，通过 WSL localhost 转发连接。沿用原证书和令牌时，不需要重新配置模型。若已设置 Tailscale Serve，目标端口保持一致，但必须实测连接；不能把本机健康检查当作手机外网验证。

## 回退

先停 Linux 服务及 Windows 启动任务，确认端口释放，保留迁移后数据，再用原 Windows 运行版本和原工作区启动。迁移后的新对话不会自动出现在旧副本里；不能让两边同时运行，也不能把重新加密的 Linux SQLite 直接覆盖回 Windows。若启用后产生了新数据，应先单独导出或设计反向迁移。
