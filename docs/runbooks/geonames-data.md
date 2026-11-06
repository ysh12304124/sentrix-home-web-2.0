# Runbook：GeoNames 地点数据

时间线的「乡镇 / 村庄 / 国外城镇」这一级靠 GeoNames 的最近邻点，数据**不进版本控制**，
必须由部署方按本文件装。没有它服务照常启动——`GeoNamesNearestResolver.available` 为假、
`resolve_nearest` 恒返回 `None`，区县那一级（PyGeoCN 的**面**）不受影响。

## 装什么、从哪来、多大

| 文件 | URL | 字节数 | 覆盖 |
|---|---|---:|---|
| `CN.zip` | `https://download.geonames.org/export/dump/CN.zip` | 32,195,246 | 中国区全部记录（约 96 万条）：`ADM4` 乡镇 + `PPL` 村庄 |
| `cities15000.zip` | `https://download.geonames.org/export/dump/cities15000.zip` | 3,359,366 | 国外人口 ≥15000 的城镇（34,092 个可用点） |

合计 **35 MB**。153 实测到 `download.geonames.org` 的带宽是 27–34 KB/s，约 **17 分钟**。

**为什么不用 `allCountries.zip`（402 MB）**——这是本切片的明确决定，不是遗漏：

- `CN.zip` 就是 `allCountries` 按 `country_code = CN` 切出来的，两者中国区记录**完全相同**；
- 设计 §3.3 声明的能力边界是「国内到区县 + 乡镇/村庄，国外到城市/乡镇」，
  `CN.zip + cities15000.zip` 正好覆盖，且**已经超出 1.0**（1.0 国内只到区县、
  国外只到城市）；
- 按实测带宽下全量要 **3.7 小时**，而多出来的只有「国外到村庄」这一级——
  没有一条验收标准要求它。

要做「国外到村庄」才需要全量；那时的区别只是把 `allCountries.zip` 也放进同一个目录，
解析器扫目录下所有 `*.zip`，不需要改代码。

## 放哪

```
$SENTRIX_DATA_DIR/geo/          # 默认 ./data/geo/
├── CN.zip
└── cities15000.zip
```

`SENTRIX_DATA_DIR` 默认是仓库根的 `data/`，而 `/data/` 整条在 `.gitignore` 里
（那是**运行期数据目录**：`sentrix.db` / media / previews / qdrant 都派生自它）。
所以这份数据既不在仓库里，也不在 wheel 的 `package-data` 里——`package-data` 只带
随源码发布的资源（迁移 `.sql`、地名别名表 `infrastructure/places/data/aliases.json`）。
**这是有意的**：35 MB 的第三方数据属于部署产物，不属于源码。

解析器扫的是该目录下的 `*.txt` 与 `*.zip`，可以直接放解压出来的 `.txt`。

```bash
mkdir -p data/geo
cd data/geo
curl -fLO https://download.geonames.org/export/dump/CN.zip
curl -fLO https://download.geonames.org/export/dump/cities15000.zip
```

## 怎么验证装好了

```bash
cd <repo>
.venv/bin/python -c "
from pathlib import Path
from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.places.geonames import GeoNamesNearestResolver
r = GeoNamesNearestResolver(Path('data/geo'))
print('counts:', r.counts)                      # (乡镇, 国内居民点, 国外居民点)
print(r.resolve_nearest(GpsPoint(30.1839, 120.2642)))   # 萧山
print(r.resolve_nearest(GpsPoint(0.0, -160.0)))         # 太平洋正中
"
```

**应该看到**：

```
counts: (11878, 885912, 31975)
NearestPlace(township='...', locality='...', distance_m=...)
None
```

三个数对上 `counts` 就说明数据装对了：`ADM4` 11,878（设计 §3.3 的 11,450 是「中文名以
镇/乡/街道结尾」的那部分）、国内 `PPL` 885,912、国外 31,975。第三行是**半径上限**
（`MAX_NEAREST_RADIUS_M = 50_000`）生效的直接证据：太平洋正中的坐标返回 `None`，
而不是「火奴鲁鲁附近」——1.0 算了 `distance_km` 却从不读取，那是一个缺陷。

没有数据时同一段代码打印 `counts: (0, 0, 0)` 与两个 `None`，服务照常启动。

## 一个数据本身的坑（不是部署问题）

`CN.zip` 里有一个 `readme.txt`（8,843 B）排在 `CN.txt`（132,055,747 B）**前面**。
按归档顺序取第一个 `.txt` 成员会解析出 **0 个点且不报错**，整个国内最近邻静默失效。
`geonames.py` 的 `_pick_member` 按「同名成员优先、否则取最大的 `.txt`」选，
`test_zip_member_picker_skips_the_readme` 钉住它。手工检查数据时如果解压看到
`counts` 的中间那个数是 0，先看是不是拿到了 `readme.txt`。

## 许可

GeoNames 数据是 **CC BY 4.0**。分发（含内网部署）需要保留署名，见
<https://www.geonames.org/export/>。


## 2026-11-05 地点精度与资源约束

加载器逐行读取压缩包并直接分池，坐标数组预分配；不保留整份解压文本或临时坐标元组列表。
同一份 CN.zip/cities15000.zip 的独立进程实测，加载峰值 RSS 从 1575.22 MiB 降至
634.97 MiB，加载时间从 5.142 秒降至 3.197 秒（macOS，本次单轮，不代表所有设备）。
国内、国外池比较实际距离，修复边境附近国内池优先的问题；外国 ADM4 不进入国内乡镇池。

此数据提供附近居民点，不能验证道路、店铺或历史场所。全球更精细数据和磁盘空间索引
尚未完成覆盖及资源对比，本次不增加数据包。不要把地图现名或最近点视为拍摄时的确定位置。
