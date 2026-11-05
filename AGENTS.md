# Repository Guidelines

## 结构与规范

Python 网关入口为 gateway.py，quality.py 处理音质选择，publication.py 处理禁止覆盖提交；provider/ 为 Go 适配器及固定依赖补丁；tests/ 为回归测试。Python 使用四空格缩进，Go 使用 gofmt。新功能应明确失败行为和协议兼容范围。

## 验证

安装 requirements.txt 后执行 python -m unittest discover -s tests。完整音频测试需要 FFmpeg，跳过项必须注明。修改 Go 时使用 bash provider/build.sh /绝对路径/music-provider，保留固定模块补丁；Shell 脚本使用 bash -n 检查。修改 Compose 后使用示例环境运行 docker compose config --quiet。

## 贡献要求

提交描述问题、行为变化及验证结果。保留鉴权、路径校验和禁止覆盖语义，不直接修改 Navidrome 数据库。测试使用合成音频和虚构身份；不要提交凭据、媒体、数据库、个人部署参数或运行日志。依赖变更同步第三方许可与构建说明，发布材料与源码保持一致。
