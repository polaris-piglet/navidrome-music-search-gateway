# Navidrome Music Search Gateway

为 Navidrome 提供在线搜索及“收藏后下载入库”的 Subsonic/OpenSubsonic 网关。原 Navidrome 服务保持独立，支持范围内的 Subsonic/OpenSubsonic 接口代理到原服务；网关处理在线候选、音质选择、校验、入库和真实收藏。项目以源码和 Docker Compose 方式交付，不提供预构建镜像。

## 客户端连接：请选 OpenSubsonic/Subsonic

**Feishin 连接网关时不要选择 Navidrome 类型。** 当前网关不是 Navidrome 全部 API 的透明替身，也不是其网页管理入口。

| 连接目标 | Feishin 服务器类型 | 当前状态 |
| --- | --- | --- |
| 本网关 | OpenSubsonic / Subsonic（按客户端显示名称） | 当前支持路径，使用原 Navidrome 账号 |
| 本网关 | Navidrome | 暂不支持，专用登录接口可能返回 404，客户端显示 Failed to authenticate |
| 原 Navidrome | Navidrome | 保留原连接；网关不会修改它 |

这不是协议升级导致旧协议失效，而是两种连接模式使用不同接口。仅将原连接地址改成网关地址、保留 Navidrome 类型不能完成切换；类型不可编辑时新增 OpenSubsonic 连接。原服务地址不变，也无需因此重置密码。

若选对类型仍登录失败，再核对网关可达性、凭据、客户端请求与 CORS；不要将所有认证错误都归因于类型选择。原生 API 兼容仍处于[下一步评估计划](docs/roadmap.md)，尚未实现。

## 功能与限制

- 每次请求向 Navidrome 验证身份；在线功能仅开放给显式配置的账号。
- 固定用户所选来源及歌曲 ID，仅比较其关联音质；优先实际可用无损，其次有损参数。不保证平台最高音质，也不跨来源换歌。
- 下载在 SSD 校验后发布到已挂载媒体目录，通过大小与 SHA256 绑定正式 ID，最后收藏。不直接修改 Navidrome 数据库。
- 默认不登录音乐平台；已有文件不自动升级或替换，不做全库语义去重。
- 未入库候选不支持直接试听或先加入歌单；首次收藏可能较慢，客户端超时后需重试核对。
- 面向 Linux Docker 主机，当前存储检查针对 CIFS/NFS 挂载；不是任意本地目录下载器。文件系统必须支持 renameat2(RENAME_NOREPLACE)。

## 快速开始

需要 Docker Compose v2、可访问的 Navidrome、已由宿主机挂载的 NAS 目录。示例不会部署或重建 Navidrome，也不会代为挂载共享。

```sh
cp .env.example .env
# 编辑 .env：设置真实后端、允许账号、宿主目录和预期挂载来源
# 预先创建 STATE_PATH、STAGING_PATH，并为配置的 PUID/PGID 设置写权限
# MUSIC_PATH 必须位于真实的网络共享中；不要创建本地空目录替代挂载
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
```

默认入口 `http://127.0.0.1:4534`。同机客户端使用该入口；其他设备访问时需主动设置 BIND_ADDRESS，并配置网络访问控制和 HTTPS。客户端选择 Subsonic/OpenSubsonic，使用原 Navidrome 账号。浏览器前端的准确 origin 填入 CORS_ORIGINS，多个值逗号分隔。

挂载检查比较容器 /proc/self/mountinfo 的媒体路径、文件系统类型、来源和读写属性。EXPECTED_MOUNT_SOURCE 应为实际来源，例如 `//nas.example.test/audio`；NFS 示例为 `nas.example.test:/export/audio`，类型可能是 nfs4。未配置或不匹配时拒绝发布。Docker 不自动创建缺失的 bind 源目录。

不自动启动策略是显式设置：先完成实际文件系统提交和服务恢复验证，再自行决定启动编排。容器健康接口只说明 HTTP 服务可用，不代表来源解析、挂载或收藏业务健康。

## 配置与存储

所有示例都是虚构值。完整参数见 [.env.example](.env.example)。

| 配置 | 含义 |
| --- | --- |
| NAV_URL | 容器可访问的原 Navidrome URL |
| DOWNLOAD_USERS | 在线功能允许账号，留空则不开放 |
| STATE_PATH / STAGING_PATH | 状态与候选文件，建议本地 SSD |
| MUSIC_PATH | NAS 最终媒体目录，须属于 Navidrome 扫描范围 |
| EXPECTED_MOUNT_SOURCE / EXPECTED_MOUNT_TYPE | 实际挂载来源与类型 |
| PUID / PGID | 容器文件访问身份，需与挂载权限匹配 |
| CORS_ORIGINS | 允许的浏览器前端来源，不替代认证 |

状态数据库与质量报告可能含用户及歌曲信息，应视为私有运行数据，不提交版本库。仅挂载 /data、/staging、/music；不需要 Docker socket 或 Navidrome 数据库。

## 开发与验证

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
bash -n provider/build.sh
```

音频测试需要 FFmpeg，否则相关项跳过。Docker 多阶段构建内使用 Go 1.26.2 和 Python 3.13；`provider/build.sh` 复制固定依赖、应用补丁并构建，不修改共享模块缓存。手动构建 Linux 适配器：

```sh
mkdir -p bin
bash provider/build.sh "$PWD/bin/music-provider"
```

默认 amd64，可通过 TARGETARCH 指定目标架构；其他架构需要独立实测。构建需要官方基础镜像、Go 模块、PyPI 和 Debian 软件仓库可达，不内置个人代理或镜像源。基础镜像标签会随上游更新，生产部署可自行固定可信摘要。

## 文档

- [协议、事务与音质行为](docs/behavior.md)
- [验收范围](docs/testing.md)
- [下一步：Navidrome 原生 API 兼容评估](docs/roadmap.md)
- [贡献指南](CONTRIBUTING.md)
- [安全与隐私边界](SECURITY.md)
- [依赖说明](THIRD-PARTY.md)

## 许可

本项目按 [GNU AGPL v3](LICENSE) 提供；第三方组件保留各自许可。修改后的 music-lib 构建脚本、补丁及固定版本均随源码提供。使用者应遵守来源平台授权及内容使用权限；本项目不提供绕过登录或会员权限的机制。
