# bilibili_qiepian

B站直播自动录播和自动投稿后台。

这个项目用于部署在 Linux 服务器上：添加 B站主播直播间后，服务会定时检查开播状态，开播后自动录制到服务器本地，下播后按配置调用 `biliup` 自动投稿到你的 B站账号。

## 功能

- Web 后台管理主播直播间
- 自动检查 B站直播间是否开播
- 开播后自动启动录制
- 下播后自动停止录制
- 录制文件保存到本地 `recordings/`
- 可选择下播后自动投稿
- 通过 `biliup upload` 发布到你的 B站账号

## 目录

```text
app/                  后端服务
static/               Web 后台页面
data/                 数据库、日志、B站登录凭证，本地生成，不提交
recordings/           录播视频，本地生成，不提交
.env.example          配置模板
docker-compose.yml    Docker 部署配置
Dockerfile            Docker 镜像定义
```

## 部署方式一：Docker Compose

推荐用 Docker Compose 部署，依赖最少，也方便迁移。

### 1. 安装 Docker

Ubuntu / Debian 可以参考：

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

确认安装成功：

```bash
docker --version
docker compose version
```

### 2. 拉取项目

```bash
git clone https://github.com/xxjunzijun/bilibili_qiepian.git
cd bilibili_qiepian
```

### 3. 创建配置文件

```bash
cp .env.example .env
```

默认监听端口是 `8787`：

```text
APP_PORT=8787
```

录制目录和数据库目录默认已经在 `docker-compose.yml` 中映射到宿主机：

```text
./data:/app/data
./recordings:/app/recordings
```

### 4. 启动服务

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f qiepian
```

访问后台：

```text
http://服务器IP:8787
```

如果服务器开启了防火墙，需要放行端口：

```bash
sudo ufw allow 8787/tcp
```

## 配置 B站自动投稿

自动投稿依赖 `biliup`。Docker 镜像里已经安装了 `biliup`，但你需要先在服务器里登录一次 B站账号，生成投稿用的 cookie 文件。

### 1. 登录 B站账号

进入容器执行：

```bash
docker compose exec qiepian biliup --user-cookie /app/data/cookies.json login
```

按提示完成登录。成功后，宿主机会生成：

```text
./data/cookies.json
```

这个文件就是自动投稿凭证，必须保密，不要提交到 GitHub。

### 2. 测试投稿

先准备一个测试视频文件，例如：

```bash
mkdir -p recordings
```

把一个小的测试视频放到：

```text
./recordings/test.mp4
```

然后执行：

```bash
docker compose exec qiepian biliup --user-cookie /app/data/cookies.json upload \
  --copyright 2 \
  --tid 171 \
  --tag "测试" \
  --title "自动投稿测试" \
  --desc "测试投稿" \
  /app/recordings/test.mp4
```

这条命令能成功投稿后，后台的自动发布才算配置完成。

### 3. 自动投稿命令

`.env` 里默认的投稿命令是：

```text
UPLOAD_COMMAND=biliup --user-cookie ./data/cookies.json upload --copyright 2 --tid {tid} --tag "{tags}" --title "{title}" --desc "{description}" {files}
UPLOAD_RETRY_ATTEMPTS=3
UPLOAD_RETRY_DELAY_SECONDS=60
UPLOAD_DEFERRED_RETRY_ATTEMPTS=12
UPLOAD_DEFERRED_RETRY_DELAY_SECONDS=600
```

录制结束后，服务会自动替换这些变量：

```text
{file}          录制文件路径
{files}         所有分段文件路径，用于分 P 投稿
{title}         投稿标题
{description}   投稿简介
{tags}          投稿标签
{tid}           B站分区 ID
{source}        直播间地址
```

如果暂时只想录制、不想自动投稿，可以把 `.env` 里的 `UPLOAD_COMMAND` 留空。

如果 biliup 上传时遇到临时网络问题，比如 DNS 解析失败、连接失败，服务会先按 `UPLOAD_RETRY_ATTEMPTS` 连续重试。连续重试仍失败时，如果错误属于 DNS 或连接类临时错误，任务会重新回到“等待投稿”，并按 `UPLOAD_DEFERRED_RETRY_DELAY_SECONDS` 延后再次尝试。最多延后重试 `UPLOAD_DEFERRED_RETRY_ATTEMPTS` 轮。

### 4. 录制清晰度

`.env` 里的录制命令默认使用主播配置里的清晰度：

```text
RECORD_COMMAND=streamlink --retry-streams 60 --retry-max 3 "{url}" "{quality}" -o "{output}"
FFMPEG_COMMAND=ffmpeg
VIDEO_TRANSCODE_MODE=copy
VIDEO_TRANSCODE_CRF=28
VIDEO_TRANSCODE_PRESET=veryfast
VIDEO_AUDIO_BITRATE=128k
```

`{quality}` 会由后台添加主播时选择的清晰度替换，例如 `best`、`1080p`、`720p`。

如果开启录制分段，下播后 `{files}` 会包含所有分段文件，`biliup` 会把它们作为同一个稿件的多 P 上传。老配置如果还在使用 `"{file}"`，只会稳定上传第一个文件，建议更新为 `{files}`。

录制完成后服务会用 `ffmpeg -c copy` 自动把 `.ts` 封装成 `.mp4`，用于网页预览和投稿。这个过程不重编码，速度通常很快，画质不变。如果 systemd 找不到 ffmpeg，可以把 `.env` 改成绝对路径：

```text
FFMPEG_COMMAND=/usr/bin/ffmpeg
```

如果想降低视频体积，可以开启转码压缩：

```text
VIDEO_TRANSCODE_MODE=h264
VIDEO_TRANSCODE_CRF=28
VIDEO_TRANSCODE_PRESET=veryfast
VIDEO_AUDIO_BITRATE=128k
```

`CRF` 越大体积越小、画质越低，常用范围是 `23` 到 `30`。建议先用 `28`，如果觉得糊就改成 `26` 或 `24`。开启压缩后会明显增加 CPU 占用，录制结束后的 MP4 生成时间也会变长；录制中的 `.ts` 原文件仍会保留，网页预览和投稿会优先使用压缩后的 `.mp4`。

网页后台的“最近录制”里也可以给单条录制选择 MP4 品质：

```text
默认配置：使用 .env 里的 VIDEO_TRANSCODE_* 配置
原样封装：最快，体积基本不变
小体积：CRF 30，体积更小，画质损失更多
均衡：CRF 28
高质量：CRF 24，体积更大，画质更好
```

选择后点击“生成 MP4 预览”“重新生成 MP4”或“手动投稿”即可生效。手动投稿时如果还没有 MP4，会先按选中的品质生成 MP4，再进入 biliup 投稿。

## 使用后台

打开：

```text
http://服务器IP:8787
```

添加主播时，基础信息只需要填：

```text
主播名称
B站直播间链接或房间号
录制清晰度
录制分段
```

例如：

```text
主播名称：某某主播
B站直播间：https://live.bilibili.com/123456
录制清晰度：最高可用 best
录制分段：每 2 小时一段
```

清晰度可以选择：

```text
best     最高可用清晰度，文件最大
1080p    优先录 1080p
720p     更省空间
480p     文件更小
worst    最低可用清晰度
```

不同直播间实际可用的清晰度由 B站和 `streamlink` 返回结果决定。如果选择的清晰度不可用，可以改成 `best` 或 `720p` 再试。

勾选“下播后自动投稿”后：

```text
开播 -> 自动录制
下播 -> 停止录制
录制完成 -> 加入投稿队列
投稿队列 -> 调用 biliup 上传
```

状态说明：

```text
录制中 / 等待下播     streamlink 正在录制，投稿还不会开始
录制失败             streamlink 已退出，录制没有成功持续
录制中断             服务重启或进程丢失，无法确认录制还在进行
录制完成 / 等待投稿   直播已结束，等待 biliup 上传
投稿中               biliup 正在上传
已投稿               投稿命令执行成功
投稿失败             biliup 上传失败，可以查看发布输出
不自动投稿           这个主播只保存本地录播文件
```

页面显示的开始/结束时间使用服务器本地时间。请确认服务器时区正确，例如中国大陆服务器通常应为 `Asia/Shanghai`。

每条录制记录会显示一个 `.log` 日志路径。若页面显示“录制中”但服务器网口没有明显流量，通常说明 `streamlink` 没有真正拉到直播流，下一轮检查会把它标记为“录制失败”。可以在服务器上查看对应日志：

```bash
tail -n 100 /path/to/recordings/主播名/录制文件.log
```

录制中的记录会显示“服务器下行”速率。这个值来自 Linux 的 `/proc/net/dev`，默认统计默认路由所在网卡，不是单个 `streamlink` 进程的精确流量。如果服务器没有其他大流量任务，它可以用来判断当前录制是否真的在拉直播流。

录制中的记录也会显示当前录制文件大小和写入速度。这个值来自录制文件本身，比网卡流量更适合判断是否真的在落盘。

如果 B站状态接口偶发网络或 SSL 抖动，页面会显示“状态检查异常”。这只表示开播/下播检测暂时失败，不代表 `streamlink` 录制失败；只要文件大小持续增长，录制仍在继续。

如果页面显示的网卡和你在系统里看的网卡不一致，可以在 `.env` 里指定：

```text
APP_NETWORK_INTERFACE=eth0
```

查看服务器网卡名：

```bash
ip route get 1.1.1.1
ip -br addr
```

“投稿设置”里可以调整：

```text
投稿分区 tid
投稿标签
标题模板
简介模板
```

模板支持：

```text
{streamer}   主播名称
{date}       当前日期
{title}      直播间标题
{url}        直播间地址
```

“最近录制”里可以删除已结束的录制记录。删除时会同时删除本地视频文件；正在录制中的任务不能删除。

录制完成后，“最近录制”会显示 MP4 预览按钮。如果历史记录还没有 MP4，可以点击“生成 MP4 预览”。录制中的任务可以点击“中断并暂停主播”，这会停止当前 `streamlink` 进程，并暂停该主播，避免下一轮检测又重新开录。

## 部署方式二：原生 Python + systemd

如果不想使用 Docker，也可以直接部署在 Linux 系统上。

### 1. 安装依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git
```

### 2. 拉取项目并安装 Python 包

```bash
git clone https://github.com/xxjunzijun/bilibili_qiepian.git
cd bilibili_qiepian
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install streamlink biliup
cp .env.example .env
```

### 3. 启动测试

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8787
```

访问：

```text
http://服务器IP:8787
```

### 4. 配置 systemd

创建服务文件：

```bash
sudo nano /etc/systemd/system/bilibili-qiepian.service
```

写入以下内容，注意把 `/opt/bilibili_qiepian` 换成你的真实项目路径：

```ini
[Unit]
Description=Bilibili Qiepian Recorder
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/bilibili_qiepian
EnvironmentFile=/opt/bilibili_qiepian/.env
ExecStart=/opt/bilibili_qiepian/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8787
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bilibili-qiepian
sudo systemctl status bilibili-qiepian
```

查看日志：

```bash
journalctl -u bilibili-qiepian -f
```

原生部署时登录 B站：

```bash
source .venv/bin/activate
biliup --user-cookie ./data/cookies.json login
```

## 常用命令

Docker 部署：

```bash
docker compose ps
docker compose logs -f qiepian
docker compose restart qiepian
docker compose down
docker compose up -d --build
```

原生部署：

```bash
sudo systemctl status bilibili-qiepian
sudo systemctl restart bilibili-qiepian
journalctl -u bilibili-qiepian -f
```

## 数据和安全

以下内容不要提交到 GitHub：

```text
.env
data/
recordings/
*.log
cookies.json
```

当前版本暂时没有后台登录认证。如果服务部署在公网，建议先只绑定内网，或者使用 Nginx / Caddy 加基础认证、反向代理和 HTTPS。

## 故障排查

### 后台打不开

检查服务是否启动：

```bash
docker compose ps
docker compose logs -f qiepian
```

检查端口是否放行：

```bash
sudo ufw status
```

### 检查开播失败

可能是服务器网络访问 B站接口失败，查看日志：

```bash
docker compose logs -f qiepian
```

### 录制失败

先在服务器里手动测试录制命令：

```bash
docker compose exec qiepian streamlink "https://live.bilibili.com/直播间号" best -o /app/recordings/test.ts
```

### 自动投稿失败

先确认 cookie 文件存在：

```bash
ls -lh data/cookies.json
```

再手动执行一次 `biliup upload`，确认 B站账号登录状态和投稿参数没有问题。

## 后续计划

- 后台登录保护
- 投稿日志详情
- 手动重试投稿
- 投稿封面
- 长直播自动分段
- 弹幕录制和弹幕压制
