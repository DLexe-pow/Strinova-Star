# Strinova Star

卡拉彼丘（Calabiyau / Strinova）录像高光切片工具。

链路已经打通：**选录像 → 找 Tab 计分板认双方阵容 → 找击杀/击倒播报 → ffmpeg 无损切片**，界面里点按钮就行；角色识别 ORB + 几何校验 20/20，单测 225 条。另外开始加「游戏里实时录制」：目前能**手动/全局录制**（分段写盘、停止时拼成整段），录完可以直接当录像来分析。

## 现在能做什么

```bat
start-ui.bat                             :: 双击这个也行：自动找 Python 再起界面
python src/app.py                        :: 图形界面（127.0.0.1:8765，自动开浏览器）
python src/run.py avatars                :: 局内真值自检（默认 ORB）
python src/run.py avatars --method sift  :: 换 SIFT
python src/run.py avatars --method surf  :: SURF（要 opencv-contrib，本机没有）
python src/run.py avatars --method hist  :: 旧的直方图方案（最快）
python src/run.py avatars --list         :: 看本机哪种算法能用
python src/run.py avatars --truth lineup
python src/run.py avatars --assign       :: 一人一角色的整体分配
python src/run.py board 截图.png --json   :: 单张计分板 → 双方昵称 + 角色
python src/run.py feed 录像.mp4 --name 昵称 :: 找击杀/击倒，存帧到 output/frames/
python src/run.py feed 录像.mp4 --name 昵称 --save-events events.json  :: 顺手存事件清单
python src/run.py clip 录像.mp4 --events events.json --past 5 --future 5  :: 前后各 5 秒切片
python src/run.py clip 录像.mp4 --events events.json --dry-run            :: 只看窗口，不真切
python src/run_tests.py                  :: 138 条单测
```

`run.py avatars` 把 `assets/characters/lineup/`（顶部阵容条，10 张）和 `assets/characters/roster/`（Tab 计分板行，10 张）当待识别图片，跟 `assets/characters/halfbody/` 的 24 张**半身立绘**比对。

`start-ui.bat` 按 `STRINOVA_PYTHON` → 项目自带的 `python\python.exe` → `D:\software\Python\python.exe` → 项目 `.venv` → PATH 上的 `python` 顺序找一个装了 `cv2`（最好还有 `rapidocr_onnxruntime`）的解释器；**`python\` 是随项目走的一份 Python 3.13**（opencv 5.0 + onnxruntime + rapidocr + pyaudiowpatch 都装好了，约 1 GB），打包发给别人时一起带上就不用对方装环境；8765 已经有人监听就直接开浏览器，不会起第二个。参数照传：`start-ui.bat --port 9000`。批处理内容故意全英文——中文写进 `.bat` 会随控制台代码页乱码，界面本身是中文的。

界面里切录像文件夹不用手打路径：录像卡片右上角点「选文件夹…」，直接弹 Windows 自己的文件夹选择框（左边有「此电脑」和各盘符，能随便跨盘走），选好「选择文件夹」就写进设置并刷新列表。走的是 `POST /api/pick-folder`，服务端用 `src/strinova_star/picker.py` 调 `IFileOpenDialog`（ctypes 直接走 COM，本机嵌入式 Python 没有 tkinter）；这个请求会一直挂到用户选完或点取消，取消时返回空路径、设置不动。同一时间只允许开一个选择窗口。

## 界面怎么排的

顶栏中间是 **主页 / 设置** 两个页签（也可以理解成两个页面切换），右边是状态灯和窗口按钮。

**主页**（干活的地方，只放和这次操作有关的）
1. 左栏「录像与播放」：播放器 + 时间轴（击杀/击倒标记、缩放）+ 这个文件夹的录像列表；角上有「选文件夹…」「重新扫描」。
2. 左栏「结果」：双方阵容 / 击杀·击倒 / 切片产物（调试模式多一个日志），标签带角标。
3. 右栏「识别与切片」：识别双方阵容、找我的击杀、按秒数切片，下面有进度条和一行状态。
4. 右栏「录制」：未开始/录制中 + 计时、模式下拉、开始/停止、录系统声音开关。

**设置页**（不常用的参数都挪到这里）
1. 「识别与切片参数」：昵称、击杀前/后秒数，折叠里是合并间隔、采样帧率、ffmpeg、录像文件夹，底部「保存设置」。
2. 「界面」：用户/调试滑块（用户模式隐藏匹配点、采样帧率、日志页签、顶栏路径）。
3. 「自动找新录像」：监视开关与状态。
4. 「文件都在哪」：录制原片 / 切片 / 缓存 / 设置 的位置说明。

所以主页不再堆参数，打开就是“选录像 → 点按钮 → 看结果”三件事。
## 三种算法怎么选（实测）

| 算法 | 命中 | 耗时/张 | 平均第一二名分差 | 备注 |
|---|---|---|---|---|
| **ORB**（默认） | **20/20** | 175 ms | **114 个内点** | 阵容条 10/10、计分板 10/10 |
| SIFT | 18/20 | 47 ms | 12 个内点 | 阵容条 10/10，计分板 8/10 |
| SURF | — | — | — | 本机 OpenCV 5.0 无 contrib，代码就绪、跑起来会提示装 `opencv-contrib-python` |
| 直方图（对照） | 20/20 | **1 ms** | 0.17（0~1 尺度） | 快 175 倍，但靠经验权重，换皮肤未必稳 |

SIFT 认不了计分板那一行的原因很实际：原图只有 48×38，放大 6 倍靠插值，DoG 金字塔在糊图上找不到有意义的极值，关键点只剩个位数；ORB 的 FAST 是像素邻域比较，对糊图宽容。**所以默认 ORB；要极致省 CPU 用 hist；SIFT 适合只认顶部阵容条的场景。**

ORB 的内部参数也测过（`NFEATURES`/`PATCH_SIZE`）：800/21 → 63 ms、分差 69；1200/31 → 98 ms、分差 93；2000/31 → 175 ms、分差 114。现在用最稳的那档，逐帧实时场景可以调回 800/21。

实测 **20/20 = 100%**（阵容条 10/10、计分板 10/10）。计分板那一行只有 48×38，能对上的点明显少（16–76 个内点），所以阈值不能照阵容条那套定。

## 素材从哪来

| 目录 | 内容 | 来源 |
|---|---|---|
| `assets/characters/halfbody/` | **24 张角色半身立绘（识别主模板）** | [超弦体定位](https://wiki.biligame.com/klbq/超弦体定位)，`文件:<角色>-半身图.png`（224×320，透明底） |
| `assets/characters/portraits/` | 24 张 wiki 圆形头像（128×128） | 同页面，`文件:<角色>头像.png`，备用 |
| `assets/characters/lineup/` | 10 张局内真值（顶部阵容条） | 原始帧裁切（不能带标注红框） |
| `assets/characters/roster/` | 10 张局内真值（计分板行） | 原始帧裁切 |
| `assets/weapons/icons/` | 25 把主武器剪影 | [武器筛选](https://wiki.biligame.com/klbq/武器筛选) |
| `assets/weapons/strinova-characters.json` | 24 角色 → 默认主武器/定位/阵营 | 各角色页 infobox |

## 角色识别怎么算的

`src/strinova_star/matcher_orb.py`（公共基类在 `features.py`），默认 `OrbMatcher`（ORB 特征点 + RANSAC 几何校验），三步：

1. **模板**：半身立绘取「整幅宽 × 85:52 × 顶部偏移 12%」的头部窗，按 1.0 / 1.5 / 2.0 三个尺度各提一套 ORB 描述子（立绘尺寸和局内贴图对不上，多尺度是为这个）；透明区涂黑并作为掩膜传给 `detectAndCompute`，避免在空白处提关键点。
2. **待识别**：取中心方窗，短边不足 120px 放大到 4 倍（计分板行只有 48×38，不放大提不出几个点）。
3. **打分**：`knnMatch(k=2)` + Lowe ratio 0.75 筛「好匹配」，再用 `estimateAffinePartial2D(RANSAC)` 数**内点**；每个角色取三尺度里的最大内点数。内点数 = 「有多少个特征点能对上同一个几何变换」，比匹配数可靠得多。

关键结论（都实测过，别改回去）：

- **只做比率检验、不做几何校验 → 16/20**：`令→蕾欧娜`、`星绘→梅瑞狄斯`、`绯莎→玛德蕾娜·利里`、`芙拉薇娅→蕾欧娜` 都是「匹配点不少但位置乱跳」。加 RANSAC 数内点后才 20/20 —— 这一步不能省。
- 灰度互相关 → 3D 渲染 vs 2D 立绘像素对不齐，`白墨（绿发）` 会串成 `米雪儿·李（金发）`。
- 颜色三维直方图相关 → 被最大的一簇主导，浅色头发全挤在一起，同样会串。
- 直方图那套（`HalfbodyMatcher`）留着做对比：`--method hist`，也能 20/20，但靠的是「0.75 颜色 + 0.25 亮度」的经验权重，换皮肤就未必稳；ORB 认的是特征点几何关系，对配色变化更稳。

阈值：`match(min_score=12, min_margin=4)` —— 内点少于 12 个、或第一二名差不到 4 个内点，就返回空串（宁可不认，也不认错）。

需要「一批人各自不同角色」时用 `assign()`：当成指派问题用匈牙利算法求整体最优（`solve()`，已和穷举对拍）。

## 认阵容：先看计分板，再看顶部阵容条

一局里可以开好几次 Tab，而阵亡的人头像会被红叉压住 —— 每次被压住的还不一定是同一批人。所以 `roster_scan.scan()` 会把"开着计分板"的时刻按「隔开 4 秒算另一次开 Tab」分组成若干次，每次取首尾最多两帧读完，再按昵称投票合并：票多的角色胜出，票数一样就看内点总和。实测 10-11 那局（115 秒、开了 6 次 Tab）单帧最好 8/10，投票后 **10/10**。

计分板这条路不通时用顶部阵容条：

- **一帧计分板都没有**（有人整局不按 Tab）→ `lineup.py` 均匀抽帧读场上 10 个槽位，多帧投票，只有角色没有昵称。
- **计分板有但缺头像** → 先用这局从击杀播报头像认出来的人补，再拿阵容条按"排除法"补：该侧阵容条必须全部认出来，且剩余候选人数正好等于空缺数，才一对一填上 —— 宁可不补，也不瞎补。

## 找击杀：用这局的昵称表校对播报

播报里那行字只有二十来像素高，OCR 经常会错一两个字。所以先认阵容，再把昵称表传进去：

```python
feed.read_rows(image, region, icons, recognizer, known=["若南缘", "地球大统领", ...])
```

对不上表的直接丢掉（`killer_matched=False` 的不要），比事后纠错省事。另外**开着计分板的帧直接跳过**，因为计分板会盖住左上角播报，那几秒的"没播报"不能当成"没发生"。

播报行最左边还有一个**击杀者头像**，位置固定（1920×1080 基准下距播报区左缘 40px、相对行中心 -12px、边长 36px），拿 `assets/characters/portraits` 去认，单人 130–200 ms；内点 ≥30 且领先第二名 ≥10 才算数（实测认对的人内点 60–75，第二名只有个位数，差距很大）：

```python
character, score = feed.read_character(patch, row, scale, portraits_matcher)
```

认出来的"昵称 → 角色"写进 `output/roster-cache.json`，下次认阵容时正好拿去补计分板的空缺。

**速度**：整条表头带检测的 OCR 要 1 秒左右，一帧判一次就把扫描拖到五分钟。所以找计分板这一遍也只用「只认字」（约 15 ms）：115 秒的录像认阵容从 135 秒降到 **34 秒**，6 块板照旧 10/10。认过的"哪几秒开着计分板"记进 `output/roster-cache.json`，找击杀时按时间跳过（±1.5 秒），不用再判一次。再加上 `wanted=本人昵称` —— 不是我的行连受害者名字都不认。一局 115 秒的录像，整条流程从 428 秒压到 **83 秒**（认阵容 34 秒 + 找击杀 49 秒）。


**时间轴标记**：橙色高条=击杀，蓝色矮条=击倒，鼠标停上去看时间/受害者，点一下跳到那一下。

**时间轴缩放**：`＋/－` 以播放位置为中心缩放，`‹/›` 平移，`聚焦` 直接缩到所有击杀的范围，`复位` 回整段；也可以直接在时间轴上按住拖出一段，滚轮缩放，双击复位。看切片时缩放只在片段内生效。

**切片产物也能看时间轴**：在「切片产物」里点某一段，左边播放器切到这段视频，时间轴换成**片段内时间**，只画落在这段里的击杀/击倒（点标记照样跳到那一下）；要回整段录像点播放器下面的「回到整段录像」。每段切片的时间窗和事件是切片时一起写进 `output/roster-cache.json` 的，重开软件也在。

## 切片怎么切

`src/strinova_star/clipper.py` 只做三件事：把事件时间换成窗口、把重叠的窗口并成一段、调 ffmpeg 无损切（`-c copy`，不重编码）。

**前后秒数自由调**：`plan(events, past=8, future=2)`，默认前 8 秒、后 2 秒；命令行有 `--past/--future`，界面上是「击杀前 / 击杀后」两个输入框。单个事件还能单独覆盖 —— 事件 dict 里带 `"past": 3` 就只对这一次生效。

窗口 = `[max(0, 击杀时刻 - 前), min(击杀时刻 + 后, 录像时长)]`；时刻超出录像长度的直接丢掉。

**重叠就合并**：两个窗口有重叠或首尾相接，就并成一段，取最早的起、最晚的止。

- 击杀在 10s，5 秒后又一次击杀，前后各 5 秒 → 窗口 `[5,10]`、`[10,15]` 相接 → 并成 `[5,20]` 一段 15 秒，标签 `击杀×2`。
- 想让「只差一点点」的也并，用 `join_gap`（如 0.5）：间隔不超过它的窗口一起并；默认 0 只并真正重叠/相接的。
- 一段里混了击倒和击杀，标签写 `击倒+击杀`。

切换片：`python src/run.py clip 录像.mp4 --events events.json --past 5 --future 5`，另有 `--join-gap`、`--accurate`、`--dry-run`。事件清单来自 `feed ... --save-events`。文件名自带窗口：`clip-001_击杀×2_5.0-20.0s.mp4`。

默认 `-ss` 放在 `-i` 前（输入侧 seek）：快，但从关键帧起切，起点可能差一两秒；要卡准到帧就用 `--accurate`，代价是长录像会慢。

## 游戏里实时录制（P0）

界面右边有「录制」卡片：选模式 → 按「开始录制」，再按一次「停止录制」；录制中按 **F9** 也能开始/停止，游戏全屏时不用切回桌面。

- **手动录像**：按一下录一段，停一下存一段。
- **全局录像**：点开始就一直录，直到你点停止（对局不打断）。
- 单局分割、高光录制还没做（下拉里是灰的）。

实现都在 `src/strinova_star/recorder.py`：`ffmpeg -f lavfi -i ddagrab=0` 抓桌面（`draw_mouse=0` 不画鼠标），`hevc_nvenc` 编码，**每 10 秒切成一个 `seg-XXXX.mkv`**（`-reset_timestamps 0` + `seg.csv` 记每段全局起止），停止时按 `seg.csv` 生成 concat 列表拼成一整段 `full.mkv`，然后删掉分段。这样即使录到一半断电/崩溃，已写下的 10 秒段还在。

参数都在 `output/ui-settings.json` 的 `record_*` 里（默认 120 帧、CQ 26、关键帧 2 秒、分段 10 秒、不录鼠标）；音频设备目前要手动填进 `record_audio_devices`（Windows 设备名，`-f dshow -i audio="名字"`），每路一条音轨。

产物在 `output/recordings/<日期时间_模式>/`，按 `record_keep_hours`（默认 10 小时）和 `record_min_free_gb`（默认 10 GB）自动清理最旧的目录 —— 清理会避开正在录制和待拼接的目录。

**两个坑**（都踩过）：① 分段是「全局时间戳」，拼接列表里每段必须写 `duration 结束-开始`，不然拼出来的时长会翻倍；② 热键走 `RegisterHotKey`，如果游戏以管理员权限运行，普通权限下的 F9 会被系统挡掉 —— 那种情况要用管理员身份启动本软件。

## 目录

```
assets/                         识别素材（程序只读，别往这里丢输出）
    characters/
        halfbody/               24 张半身立绘 —— 识别主模板（+ halfbody.json）
        portraits/              24 张圆形头像 —— 备用（+ portraits.json）
        lineup/                 10 张顶部阵容条真值（+ _self.png）
        roster/                 10 张计分板行真值
    weapons/                    25 把武器剪影（icons/）+ 角色→武器映射 json
    templates/                  击杀播报图标（down / knockdown）
    profiles/                   标定文件（区域坐标按 1920×1080 基准存，运行时换算）
    overrides.json              人工修正表 {昵称: 角色}

src/                            代码
    strinova_star/              库（16 个模块）
        features.py             公共地基：读图/头部窗/RANSAC 打分/指派（assign、solve）
        matcher_orb.py          ORB    —— 默认
        matcher_sift.py         SIFT
        matcher_surf.py         SURF   （要 opencv-contrib，附可用性探测）
        matchers.py             注册表 + 工厂：make("orb"|"sift"|"surf")
        legacy_hist.py          旧的直方图方案（--method hist）
        avatars.py              兼容层：老代码 from ...avatars import 仍可用
        video.py                取帧（Video：frame(秒) / frames(区间)）
        config.py               标定文件加载与校验
        geometry.py             1920×1080 基准坐标 → 实际分辨率（含 for_size 自适应）
        ocr.py                  RapidOCR 薄封装（整段识别 / 只识别）
        scoreboard.py           单帧计分板：OCR 昵称 → 头像裁片 → 角色
        roster_scan.py          在整段录像里找 Tab 计分板（多次采样、按昵称投票合并）
        lineup.py               顶部阵容条：10 个槽位抽帧投票，兜底认角色
        feed.py                 左上角击杀/击倒播报（含击杀人头像认角色）
        clipper.py              ffmpeg 无损切片（前后秒数可调、重叠自动合并）
        watcher.py              录像录完检测（轮询，不用 watchdog）
        picker.py               Windows「选择文件夹」系统对话框（ctypes 走 COM）
        ui.py                   本地 HTTP + SSE（界面后端）
    run.py                      命令行自检（avatars / board / feed / clip）
    app.py                      图形界面入口（python src/app.py）
    run_tests.py                单测入口（这台机器的 python 是嵌入式版，忽略 PYTHONPATH）

ui/                             界面
    web/index.html              单页前端（原生 JS，无框架）
    desktop/                    Electron 外壳（main.js + package.json）

output/                         运行产物，不进版本库
    clips/                      切出来的高光
    frames/                     抽帧（feed 调试）
    ui-settings.json            界面上次的设置
    .watcher-state.json         已自动处理过的录像列表

docs/                           进展记录 + 对照图（images/）
tests/                          单测（138 条）
requirements.txt                运行依赖（opencv / numpy / rapidocr；SURF 另装 contrib）
scratch/                        调试留档，随时可删（不进版本库）
```

三种算法接口一致（都继承 `features.FeatureMatcher`）：`names` / `score_row(image)` / `scores(image)` / `match(image)`；写新算法只要继承这个基类、实现 `create_detector()` 和 `create_matcher()` 两个方法，再在 `matchers.MATCHERS` 里登记一行即可。

## 已知坑

- `cv2.imread/imwrite` 在 Windows 上不认中文路径 → 一律 `np.fromfile + cv2.imdecode` / `cv2.imencode + write_bytes`。
- 这台机器的 python 忽略 `PYTHONPATH`，`unittest discover` 拿不到包 → `src/run_tests.py` 显式逐文件导入，测试必须写成 `TestCase` 类（`unittest` 不收集裸函数）。
- 真值裁片别用带标注框的截图：红框会污染直方图（曾经因此把「白墨」测成错的）。
- 解释器：优先 `python\python.exe`（项目内自带，3.13.15，opencv 5.0.0 / onnxruntime 1.31.1 / rapidocr / pyaudiowpatch 都装好）；它就是从 `D:\software\Python` 复制来的，两边装的是同一批包，所以改完代码用 `python\python.exe -B src\run_tests.py` 或 `D:\software\Python\python.exe -B src\run_tests.py` 都行。
## 界面：简洁大方 + 三个角色主题

设计语言是「一屏一件事、靠留白分层」，不做装饰：一个主色、白底卡片、1px 浅边、12px 圆角。

**主题**（设置页 →「界面」→「主题」，点一下立刻换，记在 `ui-settings.json` 的 `theme`）

| 主题 | 取自 | 主色 | 底色 | 感觉 |
|---|---|---|---|---|
| 星绘 | `assets/characters/portraits/星绘.png` | 淡紫 `#6f5fa6` | 冷白 `#f7f7f9` | 默认，纸白 + 淡紫 |
| 米雪儿·李 | `米雪儿·李.png` | 墨蓝 `#3f4d7a` | 暖米 `#f8f5f0` | 深蓝配米色，偏沉稳 |
| 香奈美 | `香奈美.png` | 酒红 `#a2395a` | 粉白 `#fbf4f6` | 玫红配粉白，偏热烈 |

实现上只换一组 CSS 变量：`html[data-theme="xinghui|michelle|kanami"]` 覆盖 `--bg / --accent / --accent-hover / --accent-soft / --line / --line2`，布局、间距、字号全都不动，所以换主题不会错位。
主题名在首屏渲染前就先读 `localStorage` 套上（`<head>` 里一行内联脚本），避免刷新时先闪一下旧配色。

其它：

- **两个页面**：顶栏「主页 / 设置」。主页只放干活的东西（录像与播放、结果、识别与切片、录制）；参数、主题、界面模式、自动监视、文件位置都在设置页。
- **设置项是行式表单**：标签固定 132px、控件右对齐成一条竖线，说明灰字跟在下面一行。
- **按钮分三级**：主操作实心满宽、次操作描边、其余文字按钮；同一卡片只有一个实心按钮。
- **时间轴标记**：击杀玫红 `#b23b4e`、击倒靛蓝 `#5b7fb0`（颜色 + 形状双区分，不随主题变，保证一眼能认）。
- 窗口无边框，标题栏自绘（可拖、双击最大化），右侧最小化/最大化/关闭走 `preload.js` 的 IPC。
## 可读性（对比度）

主题以好看为前提也做了可读性约束，主要文字组合都达到 WCAG AA（4.5:1）：

| 用途 | 前景 / 背景 | 对比度 |
|---|---|---|
| 正文 | `#241d2e` / `#fffdfe` | 16.1 |
| 次要文字 | `#4a4258` / `#fffdfe` | 9.4 |
| 弱化文字（时间、分数、文件名大小） | `#6b6279` / `#fffdfe` | 5.7 |
| 提示文字 | `#5e566d` / `#fffdfe` | 6.9 |
| 主按钮白字 | `#ffffff` / `#6d559f` | 6.1 |
| 录制按钮白字 | `#ffffff` / `#a2516d` | 5.5 |

最小字号从 11px 提到 11.5–12.5px；击倒/击杀徽章、标签角标同步放大一档。
- 顶栏的文件路径只在**调试模式**显示；用户模式只留品牌与状态灯。
- 「设置」卡右上角的 用户/调试 是一个**滑块开关**：点左半格或右半格都能切，滑块会滑过去；状态存在 output/ui-settings.json 的 mode 里，重启仍保留。
## 出错了怎么查

日志不是只记「点了什么按钮」，而是把**出错那一刻的上下文**一起落盘：

- 位置：`data\logs\app-YYYYMMDD.log`（开发时是 `output\logs\`），按天一个文件，只留最近 7 天。
- 记录内容：每一步操作、警告、以及**任何未捕获异常的完整 traceback**（主线程、后台线程、任务线程都挂钩）。
- 任务失败时，traceback 同时写进 `job.traceback` 存到内存，界面上「识别与切片」卡片会多出一个 **导出日志** 按钮（右上角状态栏也会显示错误原因）。设置页「文件都在哪」里也有一个常驻的导出入口。
- 点「导出日志」= 下载 `GET /api/log/export` 生成的 txt，里面有：软件版本 / Python / 系统 / OpenCV 版本、当前任务名与错误、**完整 traceback**、设置快照（不含 ffmpeg 路径）、最近 200 条操作、程序日志尾部 400 行。
## 按回合分 P（小回合）

一局（大对局）里有很多「小回合」，看录像时最想直接跳到某一回合。做法是读 HUD 正上方那条：

```
        目标7回合
   00     00:05     02
```

左/右两个数字是**双方回合比分**，中间是**回合倒计时**。比分只要一变，就说明上一回合结束了；
倒计时回到满值（`≥ 105 秒`）就是下一回合真正开始。所以：

- 每秒采一帧、只读这三小块（识别一次约 10 ms），六分半的录像约 45 秒扫完；
- 比分**只增不减**，掉下来多是切场景时读到了别的数字，所以一个变化要连续两帧还在才算；
- 小于 20 秒的「回合」当噪声并进前一个；
- 最后一小段（短于 60 秒、比分又刚跳过）判成**结算/回大厅**，不算回合，只留一个结束点。

界面里是「回合」页签：一行一个回合（起止时间、时长、比分），点一下播放器直接跳到那一段；页签上带回合数角标。
命令行也能跑：`python src/run.py rounds <录像>`（`--step` 调采样间隔，`--json` 出结构化结果）。
## 版本号与发版

**规矩：版本号只在用户说「发版」时才动，平时不自己加版本、不自己写更新日志。**

发一个版本要做的事：

1. 改 `src/strinova_star/version.py` 的 `VERSION` / `BUILD`（唯一来源；界面标题栏、设置页「关于」、导出日志都读它）。
2. 在 `CHANGELOG.md` 最上面加一节，写清这个版本改了什么。
3. 打包到 `release\vX.Y.Z\`：
   - `release\vX.Y.Z\Strinova Star\` —— 绿色版文件夹，双击 `Strinova Star.exe`
   - `release\vX.Y.Z\StrinovaStar-vX.Y.Z.zip` —— 同一个文件夹的压缩包，发给别人
4. 校验：双击 exe 能起、`/api/state` 200、设置页「关于」显示的版本对得上。

> 为什么要在工作区外面组装再挪进来：这个工作区的新建文件会继承「低完整性级别」，Electron 以低完整性启动会直接崩（`0x80000003`），所以先在 `D:\StrinovaStar-build` 装好、再 `Move-Item` 进来。

**目录长这样**

```
release\
  v0.1.0\
    Strinova Star\            ← 绿色版程序（687 MB）
    StrinovaStar-v0.1.0.zip   ← 分发包（285 MB）
```

历史版本都留在 `release\` 里，不要覆盖。
## 从源码运行

需要 Python 3.13 和 ffmpeg。

```bat
pip install -r requirements.txt
python src\app.py          :: 起界面（默认 http://127.0.0.1:8765）
python src\run_tests.py    :: 跑单测
```

打包好的绿色版在 [Releases](https://github.com/DLexe-pow/Strinova-Star/releases)，解压双击 `Strinova Star.exe` 即可，自带 Python 和 ffmpeg。

## 素材（重要）

仓库里**只放代码、标定档和三个播报小图标**，不放游戏里的角色立绘 / 武器图标 —— 那些是卡拉彼丘（Strinova）的版权素材，不随仓库分发。

没有这些图，识别功能会报「模板目录不存在」。自己补：在游戏里截图裁一下，按名字放进对应目录（尺寸见 `ASSETS.md`）：

```
assets\characters\halfbody\<角色名>.png    224x320  透明底，取到胸口
assets\characters\portraits\<角色名>.png   128x128  头像
assets\characters\lineup\<角色名>.png      85x52    顶部阵容条那格
assets\characters\roster\<角色名>.png      52x34    计分板小头像
assets\weapons\icons\<武器名>.png          180x82
```

## 免责声明

- **代码**：MIT，见 `LICENSE`。
- **游戏素材**：卡拉彼丘 / Strinova 的角色形象、图标、名称版权归开发商所有；本仓库不包含这些素材，使用者自备。
- **用途**：本工具只读屏幕与本地录像文件，不注入进程、不读写游戏内存、不模拟操作；请遵守游戏用户协议，仅用于个人离线整理。
- 本项目与游戏官方无关。