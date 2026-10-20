"""站点内容层：**所有文案与事实的唯一来源**。

为什么单独一层
--------------
1. 站点上的每个数字都能被复核（代码行数、用例数、快捷键条数都是仓库实测值）；
2. 本模块**不 import flet**，所以能在无渲染环境里直接导入做断言；
3. 视图层只负责"怎么摆"，改文案不必碰组件代码。

事实来源
--------
- 代码规模：仓库实测（排除 `build/`、`docs/`、`__pycache__`）。
- 特性 / 块级 / 行内格式：`README.md`。
- 配置项：`config/settings.py::DEFAULT_SETTINGS`。
- 技术栈版本：`pyproject.toml [project].dependencies`。
- 快捷键：`services/shortcuts.py::ACTION_REGISTRY`（由 `_shortcuts.py` 运行时读取）。
- 版本 / 作者 / 许可：`config/app_meta.py`（与编辑器「关于」页同源）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 产品标识（静态回退值；运行时由 __init__.inject_runtime_facts 覆盖版本号）
# ---------------------------------------------------------------------------

APP_NAME = "CS Markdown Editor"
APP_SUBTITLE = "光标级所见即所得的 Markdown 编辑器"
APP_TAGLINE = "Typora 风格的桌面 Markdown 编辑器"
APP_LICENSE = "MIT"
APP_AUTHOR = "CSTOS"
APP_COMPANY = "ShawnChen"
REPO_URL = "https://example.com"  # TODO: 换成正式仓库地址后「关于」区同步生效

#: 版本号 / 运行环境摘要：由 `cmsite.inject_runtime_facts()` 在启动时写入
RUNTIME: dict[str, str] = {"version": "0.1.0", "runtime": "Python 3.12 · Flet 1.0.4"}

#: 打开站点时落在哪个区块（由 `cmsite.set_start_anchor()` 写入；None = 首屏）
START_ANCHOR: str | None = None

#: 打开站点时的主题（"light" / "dark"；由 `cmsite.set_start_theme()` 写入）
START_THEME: str = "light"

#: 桌面模式下的初始窗口尺寸（Web 模式下由浏览器决定）
START_SIZE: tuple[int, int] = (1280, 860)

HERO_LEAD = (
    "底层始终以渲染样式呈现文档，顶层透明输入框承载光标。"
    "输入的字即时落到文档，光标与字形像素级对齐——没有预览按钮，也没有源码与预览的分栏。"
)
HERO_NOTE = "纯 Python 声明式 UI · 不依赖 Web 前端 · Windows 桌面优先"

# ---------------------------------------------------------------------------
# 导航
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NavItem:
    """顶栏导航项；`anchor` 同时是区块的 `ScrollKey`。"""

    label: str
    anchor: str


NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("概览", "hero"),
    NavItem("特性", "features"),
    NavItem("演示", "demo"),
    NavItem("文档", "docs"),
    NavItem("架构", "architecture"),
    NavItem("关于", "about"),
)

# ---------------------------------------------------------------------------
# 首屏指标（仓库实测）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Metric:
    value: str
    label: str
    hint: str


METRICS: tuple[Metric, ...] = (
    Metric("71,829", "行 Python 代码", "224 个源文件"),
    Metric("1,747", "条单元测试", "96 个测试文件"),
    Metric("51", "个可改键动作", "浏览态 / 编辑态两层"),
    Metric("10", "种块级语法", "全部点击即编辑"),
)

# ---------------------------------------------------------------------------
# 特性卡片（icon 用 `ft.Icons` 成员名，由视图层 getattr 解析）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Feature:
    icon: str
    title: str
    summary: str
    points: tuple[str, ...] = ()


FEATURES: tuple[Feature, ...] = (
    Feature(
        "BOLT",
        "光标级实时渲染",
        "每敲一个字符就重新解析当前行并立即渲染，语法标记在你离开这段之后透明。",
        (
            "`#`、`**` 与反引号只在光标所在段变灰可见",
            "链接与图片的 URL 按光标位置自动折叠",
            "浏览器式「先写源码再看效果」的割裂被彻底去掉",
        ),
    ),
    Feature(
        "KEYBOARD",
        "中文输入法不打断",
        "透明输入框不设 value，靠异步清空内部值，IME 组合态不会被重渲染撕断。",
        (
            "同行输入不重建输入框（key 基于行号 + 会话号）",
            "拼音上屏、候选词切换、连续输入均不丢字",
            "跨行移动后自动重定位光标，不闪不跳",
        ),
    ),
    Feature(
        "STRAIGHTEN",
        "像素级光标对齐",
        "用 HarfBuzz（与 Skia 同引擎）整形测量每个字形的 advance，而不是估字宽。",
        (
            "渲染层与光标层共用同一 StrutStyle 钉死行高",
            "补偿 Flet 默认 letter_spacing 0.25",
            "CJK 字形回退按字符边界切分测量",
        ),
    ),
    Feature(
        "SPACE_DASHBOARD",
        "多文档标签与拆分",
        "顶栏标签并行编辑多个文件，`Ctrl+\\` 向右拆分出第二组独立标签。",
        (
            "同一文件左右各开一份时共享同一个文档对象",
            "两侧编辑实时同步，光标与撤销历史各自独立",
            "拆分组各自新建 / 关闭 / 循环，互不干扰",
        ),
    ),
    Feature(
        "DIFFERENCE",
        "文件对比",
        "把两份文档并排成标签，行级差异着色，两侧都能直接改。",
        (
            "绿色新增 / 红色删除 / 灰色修改",
            "缺失侧用行高间隙对齐，视觉一一对应",
            "像素级同步滚动，一侧到底另一侧仍可独立滚动",
        ),
    ),
    Feature(
        "ACCOUNT_TREE",
        "Git 版本管理",
        "侧边栏内置源代码管理面板，不装插件也能暂存、提交、看历史。",
        (
            "暂存 / 取消暂存 / 丢弃三分支语义",
            "内嵌统一与分栏两种差异视图",
            "分支切换、合并、领先落后计数、冲突守卫",
        ),
    ),
    Feature(
        "FOLDER_OPEN",
        "文件树与全局搜索",
        "打开一个文件夹作为工作区，文件树随磁盘变化自动刷新。",
        (
            "支持指向 .md 的 Windows 快捷方式（.lnk）",
            "后台轮询比对签名，零第三方依赖",
            "跨文件查找与替换，正则 / 大小写 / 整词可组合",
        ),
    ),
    Feature(
        "TABLE_CHART",
        "表格与代码块岛屿",
        "表格和代码块是自管状态的独立岛屿，不参与段落的激活/草稿系统。",
        (
            "单元格单击即编辑，行高恒定、文字左缘不跳",
            "代码块语法高亮 + 行号 + 语言切换 + 折叠",
            "`Tab` / `Enter` 单元格导航，末格自动新增",
        ),
    ),
    Feature(
        "TUNE",
        "键位、主题与备份",
        "51 个动作全部可改键，冲突实时检测；主题、字号、行高、宽度都可调。",
        (
            "快捷键支持导入导出与恢复默认",
            "定时备份 + 崩溃恢复（未命名草稿也保得住）",
            "导出 HTML，保留表格、脚注、任务列表",
        ),
    ),
)

# ---------------------------------------------------------------------------
# 块级支持
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Block:
    syntax: str
    name: str
    desc: str


BLOCKS: tuple[Block, ...] = (
    Block("#", "标题 H1–H6", "六级字号与字重递进，色阶红橙绿青蓝紫；Ctrl+0~6 切换"),
    Block("-", "无序列表", "圆点按缩进层级着色，Tab / Shift+Tab 调整层级"),
    Block("1.", "有序列表", "缩进后序号重置为 1，嵌套子列表自然计数"),
    Block("- [ ]", "任务列表", "点击复选框切换，已勾选内容删除线 + 淡化"),
    Block(">", "引用", "多层嵌套，左侧逐层彩色边框，层级可增删"),
    Block("```", "代码块", "Pygments 分词高亮、行号、语言下拉、折叠、复制"),
    Block("$$", "公式块", "浏览态渲染 LaTeX，点击同时显示源码与实时预览"),
    Block("---", "分隔线", "三种写法通用，行首按 Backspace 转为空行"),
    Block("[toc]", "目录卡片", "按标题级别生成，点击跳转并高亮脉冲"),
    Block("|", "表格", "单元格编辑、行列增删、对齐设置、删除整表"),
)

# ---------------------------------------------------------------------------
# 行内格式
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InlineFormat:
    name: str
    syntax: str
    desc: str


INLINE_FORMATS: tuple[InlineFormat, ...] = (
    InlineFormat("加粗", "**文本**", "Ctrl+B"),
    InlineFormat("斜体", "*文本*", "Ctrl+I"),
    InlineFormat("删除线", "~~文本~~", "Ctrl+D"),
    InlineFormat("高亮", "==文本==", "Ctrl+Shift+H"),
    InlineFormat("行内代码", "`code`", "Ctrl+`"),
    InlineFormat("链接", "[文本](url)", "Ctrl+K"),
    InlineFormat("图片", "![alt](url)", "独占一行即成图"),
    InlineFormat("行内公式", "$x^2$", "Ctrl+M"),
    InlineFormat("上标", "^2^", "支持组合语法"),
    InlineFormat("下标", "~i~", "支持组合语法"),
)

# ---------------------------------------------------------------------------
# 架构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Layer:
    name: str
    duty: str
    modules: tuple[str, ...] = ()


ARCH_LAYERS: tuple[Layer, ...] = (
    Layer("main.py", "入口：最早初始化日志、注册字体与主题、page.render(App)", ("ft.run",)),
    Layer("app/", "应用层：AppContext 状态容器 + 控制器模式，唯一持有 page 全局操作权",
          ("_render", "_tab_management", "_file_io_ops", "_git_controller", "_split_editor")),
    Layer("views/", "视图层：声明式 Flet 组件（编辑器根组件包 + 顶层视图）",
          ("editor/", "line_view", "rendered_line", "sidebar", "table_view")),
    Layer("core/", "核心契约：EditorActions 数据契约、光标状态、撤销栈",
          ("actions", "cursor", "history")),
    Layer("parser/", "解析层：行级 / 段级解析、选区↔源码映射、序列化",
          ("_engine", "inline", "block", "reparse", "serialize")),
    Layer("models/", "数据模型：Segment / Line / Document 三级 observable 状态",
          ("document",)),
    Layer("services/", "服务层：快捷键、文件 IO、备份恢复、导出、Git 命令封装",
          ("shortcuts", "file_io", "backup", "git/")),
    Layer("utils/", "工具层：文本测量、段工具、表格工具（无项目内依赖的叶子层）",
          ("text_layout", "segment_helpers", "table_helpers")),
    Layer("config/ + styles.py", "配置层与设计 token：默认设置深合并、主题配色、排版常量",
          ("settings", "app_meta", "styles")),
)


@dataclass(frozen=True, slots=True)
class Decision:
    title: str
    body: str


ARCH_DECISIONS: tuple[Decision, ...] = (
    Decision(
        "Stack 双层，而非段级拼接",
        "底层 RenderedLine 负责渲染，顶层一个全透明的 TextField 负责光标与输入。"
        "相比「前段文字 + 激活段输入框 + 后段文字」的拼接布局，它没有接缝、"
        "没有宽度跳跃，光标天然落在字形中间。",
    ),
    Decision(
        "光标用 ref，画面用 state",
        "输入过程中直接改 ref 里的光标位置，不触发重渲染；只在会话结束时同步一次 state。"
        "否则每敲一个字都会重排整棵树，光标自己会抖。",
    ),
    Decision(
        "原子化重解析",
        "高频编辑路径只发一次 observable 通知（`reparse_line_atomic`），"
        "而不是逐字段改模型触发 2–7 次。大文档下这是「跟手」与「卡顿」的分界。",
    ),
    Decision(
        "行级窗口化",
        "超过 3000 行只构建视口附近的行控件，未构建区用 `ListView.padding` 留白"
        "（不能放占位控件，那会让滚动范围估算失准）。",
    ),
    Decision(
        "行级撤销快照",
        "单行编辑只存这一行的原文与光标位置，内存 O(1)/次；"
        "只有行结构变化（换行、合并、多行粘贴）才存全文快照。",
    ),
    Decision(
        "独立岛屿",
        "代码块、表格、公式、目录自管编辑状态，不接入段落的激活/草稿系统。"
        "它们的复杂度与段落完全不同，混进一套状态机只会互相拖累。",
    ),
)

#: 分层代码规模：(层名, 文件数, 行数)
LAYER_STATS: tuple[tuple[str, int, int], ...] = (
    ("tests/", 96, 26878),
    ("views/", 61, 25571),
    ("app/", 17, 7624),
    ("services/", 23, 6560),
    ("utils/", 9, 1885),
    ("parser/", 7, 1444),
    ("config/", 4, 591),
    ("core/", 4, 335),
    ("models/", 1, 233),
)

#: 架构红线（写在 AGENT.md，站点只摘维护成本最高的几条）
ARCH_RULES: tuple[str, ...] = (
    "分层单向依赖：services / parser / models / core / utils 不得反向导入 app 或 views。",
    "UI 只由状态驱动：不在渲染后调 control.update()，控件在渲染时即被冻结。",
    "透明光标框不设 value，靠 effect 异步清空，否则打断 IME 组合态。",
    "同行输入不改变输入框 key，只有撤销/重做这类强制重建才递增会话号。",
    "渲染层与光标层必须共用同一断行算法，否则换行点不一致、光标错位。",
)

# ---------------------------------------------------------------------------
# 技术栈
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TechItem:
    name: str
    version: str
    duty: str


TECH_STACK: tuple[TechItem, ...] = (
    TechItem("flet", "== 1.0.4", "声明式 UI：@ft.component / use_state / observable / memo"),
    TechItem("mistune", ">= 3.3.4", "行内 AST 解析与 HTML 导出"),
    TechItem("uharfbuzz", ">= 0.40", "文本整形测量，与 Skia 同引擎"),
    TechItem("Pygments", ">= 2.19", "代码块语法分词（惰性导入，未知语言回退纯文本）"),
    TechItem("flet-datatable2", "== 1.0.4", "表格渲染与单元格编辑"),
    TechItem("Pillow", ">= 12.3", "图片尺寸读取与等比缩放"),
    TechItem("watchdog", ">= 4.0", "外部修改检测（原生文件通知）"),
    TechItem("Python", ">= 3.12", "模型层使用 StrEnum；需要支持 PEP 695 语法"),
)

INSTALL_CMD = """git clone <repo> cs-markdown-editor
cd cs-markdown-editor

# 方式一：按 pyproject.toml 装成可编辑包（推荐）
pip install -e .
python main.py

# 方式二：只装运行时依赖
pip install flet mistune uharfbuzz pygments flet-datatable2 pillow watchdog
python main.py"""

# ---------------------------------------------------------------------------
# 文档区
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocBlock:
    """文档正文的一个块。

    `kind` 决定视图层用哪种呈现：
    `p` 段落 / `ul` 要点 / `code` 代码 / `steps` 编号步骤（items 为 (标题, 说明)）/
    `kv` 键值表（items 为 (键, 说明)）/ `note` 提示条。
    """

    kind: str
    text: str = ""
    items: tuple[tuple[str, str], ...] | tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocEntry:
    anchor: str
    title: str
    summary: str
    blocks: tuple[DocBlock, ...] = field(default_factory=tuple)


DOCS: tuple[DocEntry, ...] = (
    DocEntry(
        "docs-install",
        "安装与运行",
        "Python ≥ 3.12，Windows 优先；一条 pip 命令装完即可启动。",
        (
            DocBlock("p", "项目用 PEP 621 的 `pyproject.toml` 统一管理依赖与打包配置，"
                          "没有 `requirements.txt` 的双份漂移问题。"),
            DocBlock("kv", items=(
                ("Python", "≥ 3.12（用到 StrEnum 与 PEP 695 类型别名）"),
                ("平台", "Windows 10/11 优先；Flet 桌面运行时决定，无原生扩展编译"),
                ("依赖", "7 个运行时依赖，全部有 wheel，不需要 SDK"),
                ("入口", "`python main.py` 或安装后的 `cs-markdown-editor` 命令"),
            )),
            DocBlock("code", INSTALL_CMD),
            DocBlock("note", "首次启动会展示内置示例文档；之后启动直接打开空白页，"
                             "省掉 298 行示例的解析时间。"),
        ),
    ),
    DocEntry(
        "docs-start",
        "快速上手",
        "五步走完一条完整的写作闭环：打开、写、看、存、导出。",
        (
            DocBlock("steps", items=(
                ("打开文件夹", "`Ctrl+Shift+O` 选一个目录作为工作区，左侧文件树会记住它。"),
                ("开始写", "点击任意一行即出现光标；`# ` 开头就是标题，`- ` 开头就是列表。"),
                ("调格式", "选中文字按 `Ctrl+B` / `Ctrl+I` / `Ctrl+Shift+H`，再按一次取消。"),
                ("多文档", "`Ctrl+N` 新标签，`Ctrl+\\` 向右拆分，两组标签各管各的。"),
                ("保存与导出", "`Ctrl+S` 落盘；需要给别人看时导出 HTML。"),
            )),
            DocBlock("note", "所有快捷键都能在「设置 → 快捷键」里改：点「修改」→ 按下新组合键即可，"
                             "冲突会实时提示。"),
        ),
    ),
    DocEntry(
        "docs-config",
        "配置项",
        "设置面板的五个分区对应 `settings.json` 的键；老配置文件会被深合并补齐新键。",
        (
            DocBlock("p", "配置持久化在项目根 `settings.json`。新增默认键不会让老用户报 KeyError——"
                          "`load_settings()` 会与 `DEFAULT_SETTINGS` 深合并。"),
            DocBlock("kv", items=(
                ("content_max_width", "正文最大宽度（默认 1080）"),
                ("body_font_size / line_height", "字号与行高（默认 16 / 1.6）"),
                ("font_family", "正文字体（默认随包 Alibaba PuHuiTi 3.0）"),
                ("zoom", "界面缩放百分比，`Ctrl+Shift+=` 增减"),
                ("word_wrap", "自动换行开关，对应 `Alt+Z`"),
                ("auto_save / auto_save_interval", "自动保存与间隔（分钟）"),
                ("backup_enabled / backup_interval", "定时备份与间隔（分钟）"),
                ("backup_retention_days", "已命名文档备份保留天数"),
                ("recover_untitled_days", "未命名草稿保留天数"),
                ("detect_external_changes", "原文件被外部修改时提示重载"),
                ("code_theme_light / _dark", "代码高亮主题（GITHUB / ATOM_ONE_DARK）"),
                ("git_diff_mode", "差异视图默认模式：unified | split"),
                ("log_level", "DEBUG / INFO / WARNING / ERROR（环境变量可覆盖）"),
            )),
            DocBlock("note", "排查问题时常用 `CS_MD_LOG_DIR` 与 `CS_MD_LOG_LEVEL` 两个环境变量，"
                             "它们优先于配置文件。"),
        ),
    ),
    DocEntry(
        "docs-keys",
        "快捷键",
        "51 个动作按用途分组，默认键位与代码里的注册表一致。",
        (),
    ),
    DocEntry(
        "docs-layout",
        "项目结构",
        "九层单向依赖：越往下越不知道上层存在，越容易单独测试。",
        (
            DocBlock("code", """cs-markdown-editor/
├── main.py                入口：日志 → 字体 → 主题 → page.render(App)
├── styles.py              主题配色、段样式、标题字阶、间距 token
├── app/                   应用层：AppContext + 控制器
│   ├── _render.py         渲染树装配（侧栏 / 标签 / 编辑区 / 状态栏）
│   ├── _tab_management.py 组感知的标签增删改查
│   ├── _split_editor.py   拆分编辑器（左右独立标签组）
│   └── _git_controller.py Git 面板的调度与状态映射
├── views/                 视图层：声明式组件
│   ├── editor/            编辑器根组件包（约 20 个工厂模块）
│   ├── line_view.py       行视图（岛屿分支 + 渲染层 + 光标层）
│   ├── pixel_layout.py    像素布局缓存 + 命中测试
│   └── key_bindings.py    键盘事件分发
├── core/                  动作契约 / 光标状态 / 撤销栈
├── parser/                行级·段级解析、重解析、序列化
├── models/                Segment - Line - Document 三级状态
├── services/              快捷键 / 文件 IO / 备份 / 导出 / Git
├── utils/                 文本测量 / 段工具 / 表格工具（叶子层）
├── config/                默认设置与元信息
└── tests/                 1747 条单元测试 + 进程内渲染夹具"""),
            DocBlock("p", "依赖方向从上到下单向。`utils/` 是叶子层，不允许 import 项目内任何模块；"
                          "`parser/` 与 `models/` 不知道 UI 的存在，因此能纯函数式地做 roundtrip 测试。"),
        ),
    ),
    DocEntry(
        "docs-faq",
        "常见问题",
        "关于性能、数据安全与平台的一些直白回答。",
        (
            DocBlock("kv", items=(
                ("大文档会卡吗？",
                 "3000 行以上自动进入行级窗口化，只构建视口附近的行；"
                 "单次重渲染成本与文档总行数解耦。"),
                ("文件会不会被偷偷上传？",
                 "不会。这是本地桌面应用，除 Git 命令本身外没有任何网络请求。"),
                ("编辑时磁盘文件被改了怎么办？",
                 "监听原文件 mtime，发现外部修改会弹重载确认；"
                 "定时备份与崩溃恢复独立于自动保存，始终在后台运行。"),
                ("为什么不做成 Web 编辑器？",
                 "像素级光标与 IME 行为依赖 Flutter 的渲染管线与本机字体测量，"
                 "浏览器里做不到同样的对齐精度。"),
                ("支持 macOS / Linux 吗？",
                 "核心基于 Flet 跨平台运行时，Windows 是首选验证平台；"
                 "快捷方式（.lnk）解析与部分文件操作仅 Windows 生效。"),
            )),
        ),
    ),
)

# ---------------------------------------------------------------------------
# 演示样例（右侧用 ft.Markdown 真实渲染）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DemoSample:
    label: str
    desc: str
    source: str


DEMO_SAMPLES: tuple[DemoSample, ...] = (
    DemoSample(
        "写作",
        "标题、强调、引用与列表——最常写的那几种，渲染即时生效。",
        """# 一次只做一件事

写作时最贵的成本不是打字，是**在语法和想法之间来回切换**。

> 所见即所得的意义：让格式退到视线之外。

## 三个原则

1. 光标停在哪里，就编辑哪里
2. 语法标记只在该出现的时候出现
3. 保存这件事，不该需要你记得

这里还有 `行内代码`、*斜体*、**加粗**与 ***加粗斜体***。
""",
    ),
    DemoSample(
        "代码与公式",
        "围栏块保留语法高亮与行号；公式块渲染 LaTeX。",
        """## 渲染流程

```python
def reparse_line_atomic(doc, index, raw):
    line = build_line(raw)
    doc.lines[index] = line   # 一次通知，而非逐字段
    return line
```

行内公式 $E = mc^2$，块级公式：

$$
\\int_0^1 x^2 \\, dx = \\frac{1}{3}
$$
""",
    ),
    DemoSample(
        "表格",
        "表格是可编辑岛屿：单元格单击进入编辑，行高恒定。",
        """## 依赖一览

| 依赖 | 作用 | 版本 |
| --- | --- | --- |
| flet | 声明式 UI | 1.0.4 |
| mistune | 解析 | 3.3.4 |
| uharfbuzz | 文本测量 | 0.55 |

表格行高恒定，完整内容走悬停提示，
这样进出编辑态时不会整页抖动。
""",
    ),
    DemoSample(
        "任务与引用",
        "任务列表可点击勾选，引用支持多层嵌套。",
        """## 发布前检查

- [x] 单元测试 1747 条全绿
- [x] 拆分编辑器回归
- [ ] 打包体积复核
- [ ] 更新 CHANGELOG

> 第一层引用
> > 第二层引用，左侧边框会逐层加宽
""",
    ),
)

# ---------------------------------------------------------------------------
# 关于
# ---------------------------------------------------------------------------

ABOUT_PARAGRAPHS: tuple[str, ...] = (
    "这个项目从一个很具体的不满开始：大多数 Markdown 编辑器要么让你盯着源码，"
    "要么把编辑切成一个个「段落盒子」。前者要在大脑里预演渲染结果，后者在跨段选择、"
    "行首退格这些最基础的操作上处处别扭。",
    "于是有了 Stack 双层结构：文档始终以最终样式呈现，光标浮在最上层。"
    "代价是必须自己做文本测量、断行、命中测试与像素对齐——"
    "这也是仓库里 views/ 目录占比最高的原因。",
    "工程上刻意保持克制：七个运行时依赖、一条单向依赖链、"
    "1747 条测试把行为钉住。功能可以慢慢加，"
    "但「改了 A 结果 B 坏了」这种事，尽量在校验阶段就被拦住。",
)

ABOUT_FACTS: tuple[tuple[str, str], ...] = (
    ("作者", APP_COMPANY),
    ("组织", APP_AUTHOR),
    ("许可", APP_LICENSE),
    ("技术栈", "Python · Flet · mistune · HarfBuzz"),
)

FOOTER_NOTE = "本站同样是 Flet 声明式应用：与编辑器共用一套配色 token 与字体。"
