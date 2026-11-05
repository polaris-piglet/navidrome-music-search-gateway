# 第三方依赖

下载/搜索适配器使用 `github.com/guohuiyuan/music-lib`，固定版本见 provider/go.mod，沿用 go-music-dl 的核心依赖版本；不是自行重写平台协议。构建时下载原模块及许可文件。Python HTTP层使用aiohttp；音频校验/封装使用发行版FFmpeg。

来源：https://github.com/guohuiyuan/music-lib · https://github.com/guohuiyuan/go-music-dl · https://github.com/aio-libs/aiohttp · https://ffmpeg.org/

0.1.3通过provider/patch-library.py对固定music-lib私有构建副本新增单档音质参数（网易云、QQ、酷我）；不修改平台权限判断或共享模块缓存。provider/build.sh可复现，发行包保留原AGPL-3.0许可。

项目源码以根目录 LICENSE 中的 GNU AGPL v3 提供。music-lib 的原许可副本位于 provider/LICENSE.music-lib；Go 模块版本与校验记录位于 provider/go.mod、provider/go.sum。aiohttp、Python、Go、Debian 与 FFmpeg 保留其上游许可，容器构建不改变这些组件的许可。
