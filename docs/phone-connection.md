# 手机首次连接

电脑服务必须运行；`127.0.0.1` 在手机上表示手机自己，不能直接填写电脑浏览器里的回环地址。

## 推荐：Tailscale + 一次导入连接配置

1. 电脑安装 [Windows 版 Tailscale](https://tailscale.com/download/windows)，安装后从右下角系统托盘打开 Tailscale 登录。手机安装 [Android 版](https://tailscale.com/download/android)，打开后登录相同账号，打开连接开关并允许 VPN。
2. 电脑打开助手网页的“连接与授权”，展开“取得 Tailscale HTTPS 地址”，点“复制电脑连接命令”。在 Windows 开始菜单搜索 PowerShell 并打开，粘贴执行两行命令，无需进入项目目录。命令使用当前服务端口与安装目录；非默认安装目录需替换路径。
3. 首次若提示开启 HTTPS，打开命令输出的管理链接，开启 HTTPS（需要时开启 MagicDNS），再重新执行。也可在 [管理控制台 DNS](https://login.tailscale.com/admin/dns) 完成。第一次签发证书会公开记录设备域名，请使用不含私人身份信息的设备名称。
4. 在输出 “Available within your tailnet” 下找到并复制 `https://设备名.网络名.ts.net` 地址；不要复制 `localhost` 的上游地址。第二行 `serve status` 命令可再次查看。此地址由 Tailscale 提供；仅向私人网络开放，不需要自行买域名、创建证书或使用 Funnel。
5. 在电脑网页点“配对新手机”，设备名称填“我的手机”，地址填刚取得的 HTTPS 地址，证书方式保持“可信证书（推荐 Tailscale）”。点“生成连接配置 → 复制连接配置”。令牌由助手生成，无需自己寻找。配置包含独立设备令牌，只交给自己的手机；不要放入助手聊天、公开仓库或公开链接。
6. 将完整配置传到自己手机并复制；助手 App 点“更多 → 设置 → 粘贴连接配置”，核对地址和证书方式后保存，再同步。成功后顶部显示同步状态。可信证书的指纹为空；App 使用系统 HTTPS 校验，不跳过证书验证。
7. 同步成功后即可打开聊天、任务与资料。电脑服务、电脑 Tailscale 与手机 Tailscale 需要保持运行。电脑关机或手机断网时只能查看已同步内容、编辑草稿和暂存消息，不能执行本机任务。

Android 同时只能运行一个 VPN。如果已有 VPN 需要持续使用，可先用下面的同 Wi-Fi 方式；之后可评估带可信域名的隧道或服务器部署。此版本没有自动安装或配置 Tailscale，没有真实 iQOO 外网验证。导入减少填写步骤，不会自动建立网络。

如果导入后失去配置，在网页撤销该设备，重新配对。设备令牌当前具有用户设备权限，持有人能访问私人工作区。跨设备中转方式由用户选择，导入后清理中转副本。

参考：[Serve](https://tailscale.com/docs/features/tailscale-serve)、[Serve 命令](https://tailscale.com/docs/reference/tailscale-cli/serve)、[HTTPS 证书](https://tailscale.com/docs/how-to/set-up-https-certificates)。

## 三项信息来自哪里

| 信息 | 取得方式 |
|---|---|
| HTTPS 地址 | 推荐使用 `tailscale serve` 输出的 `.ts.net` 地址；同 Wi-Fi 时是电脑局域网 IP 和助手监听端口。不会凭空自动生成一个手机可达地址。 |
| 设备令牌 | 网页“连接与授权 → 配对新手机”签发，随连接配置提供一次；可撤销。不是电脑管理令牌 `owner.token`，也不是模型 API Key。 |
| 证书指纹 | Tailscale 可信证书不需要填写。工作区自签名证书由 `tools/workspace.py` 首次启动自动生成，SHA-256 存在私人工作区 `server-fingerprint.txt`，网页连接页也能查看。 |

手机无需安装或传输证书文件，更不能传输电脑 `server.key` 私钥。

## 同一 Wi-Fi，不使用 Tailscale

服务默认只监听电脑本机。先停止同一工作区的旧服务，再用启动脚本的 `-ListenHost '0.0.0.0'` 参数监听 IPv4 网络接口；电脑防火墙只允许私人网络内的指定端口访问。`0.0.0.0` 是监听配置，不是手机填写的地址。手机填写电脑局域网 IP，双方须能互相访问，校园网可能隔离设备。

电脑网页配对时填写 `https://电脑IP:端口`，选择“工作区自签名证书”，核对自动取得的指纹。App 可以导入同一格式的配置，也保留逐项手填入口。更换证书后必须重新核对指纹。

局域网地址会变化、路由器可能隔离手机、同一 Wi-Fi 不代表公网可达。连接失败时分别检查：电脑服务、地址端口、网络/VPN、证书校验与设备撤销状态，不以关闭 HTTPS 校验解决。
