"""站点内容层：**所有文案与事实的唯一来源**。

为什么单独一层
--------------
1. 站点上的每个数字都能被复核（代码行数、用例数、快捷键条数都是仓库实测值）；
2. 本模块**不 import flet**，所以能在无渲染环境里直接导入做断言；
3. 视图层只负责"怎么摆"，改文案不必碰组件代码。

文案风格（改之前请先读）
------------------------
说人话：一句话讲一件事，先讲用户得到什么，再讲怎么做的。
不用破折号串句子，不用"既…又…"排比，不喊口号，也不替读者下结论。
技术名词只在它本身是卖点时出现（HarfBuzz、IME、observable），其余一律换成结果描述。

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
APP_SUBTITLE = "写 Markdown，像写文档一样"
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
    "文档一直是排好版的样子，光标浮在文字上。"
    "敲下的字立刻变成结果，没有预览按钮，也没有左右分栏。"
)
HERO_NOTE = "Python + Flet · 本地运行 · 不联网"

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
    Metric("1,747", "条测试", "96 个测试文件"),
    Metric("51", "个动作可改键", "浏览态、编辑态各一套"),
    Metric("10", "种块级语法", "点一下就能改"),
)

# ---------------------------------------------------------------------------
# 特性卡片（icon 用 `ft.Icons` 成员名，由视图层 getattr 解析）
# ---------------------------------------------------------------------------

#: `summary` 是这张卡的重点句：一句话讲清「你得到什么」；
#: `points` 只放能验证的细节，两三条即可，多了就没人读了。
@dataclass(frozen=True, slots=True)
class Feature:
    icon: str
    title: str
    summary: str
    points: tuple[str, ...] = ()


FEATURES: tuple[Feature, ...] = (
    Feature(
        "BOLT",
        "边写边排，不用预览",
        "每敲一个字，当前行立刻重排，光标不移位。",
        (
            "`#`、`**` 只在光标这一行露出来",
            "链接地址离开就折叠",
        ),
    ),
    Feature(
        "KEYBOARD",
        "中文输入不打断",
        "拼音还没上屏时，界面不会重画，候选词也不跳。",
        (
            "透明输入框不设初值，避免打断组字",
            "换行、移动光标后自动归位",
        ),
    ),
    Feature(
        "STRAIGHTEN",
        "光标和字严丝合缝",
        "逐字量宽度，跟 Skia 是同一套算法。",
        (
            "渲染层与光标层共用同一行高",
            "中日韩字形按字符边界切分测量",
        ),
    ),
    Feature(
        "SPACE_DASHBOARD",
        "多标签与分屏",
        "顶栏并排开文件，`Ctrl+\\` 向右再拆一组。",
        (
            "同一文件开两份时内容实时同步",
            "光标位置和撤销记录各管各的",
        ),
    ),
    Feature(
        "DIFFERENCE",
        "两份文件对着看",
        "把两个文档并排成标签，行级标色，两边都能改。",
        (
            "绿增、红删、灰改",
            "缺失的行用留白对齐，滚动同步",
        ),
    ),
    Feature(
        "ACCOUNT_TREE",
        "自带 Git 面板",
        "侧栏里就能暂存、提交、看历史，不装插件。",
        (
            "暂存、取消暂存、丢弃",
            "切分支、合并、领先落后计数",
        ),
    ),
    Feature(
        "FOLDER_OPEN",
        "文件树与全局搜索",
        "打开一个文件夹当工作区，磁盘变了它就跟着变。",
        (
            "跨文件查找替换，可用正则、分大小写",
            "支持指向 .md 的 Windows 快捷方式",
        ),
    ),
    Feature(
        "TABLE_CHART",
        "表格与代码块",
        "这两块自己管状态，不跟着段落一起刷新。",
        (
            "单元格单击就编辑，行高不抖",
            "代码块有高亮、行号、语言切换与折叠",
        ),
    ),
    Feature(
        "TUNE",
        "键位、主题、备份",
        "51 个动作都能改键，撞车当场提示。",
        (
            "快捷键可导入导出、一键恢复默认",
            "定时备份与崩溃恢复，未命名草稿也在",
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
    Block("#", "标题 H1–H6", "六级字号字重递进，Ctrl+0~6 切换"),
    Block("-", "无序列表", "圆点按层级换色，Tab 调级"),
    Block("1.", "有序列表", "缩进后重新编号"),
    Block("- [ ]", "任务列表", "点一下勾选，勾掉的行变淡划掉"),
    Block(">", "引用", "能嵌套，左边框逐层加色"),
    Block("```", "代码块", "高亮、行号、语言选择、折叠、复制"),
    Block("$$", "公式块", "平时是公式，点开同时看到源码"),
    Block("---", "分隔线", "三种写法都认，行首退格变回空行"),
    Block("[toc]", "目录", "按标题生成，点了就跳"),
    Block("|", "表格", "改单元格、增删行列、设对齐"),
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
    InlineFormat("上标", "^2^", "可与其它语法组合"),
    InlineFormat("下标", "~i~", "可与其它语法组合"),
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
    Layer("main.py", "入口：先起日志，再装字体和主题，最后交给 App", ("ft.run",)),
    Layer("app/", "应用层：状态容器与控制器，只有这里能操作 page",
          ("_render", "_tab_management", "_file_io_ops", "_git_controller", "_split_editor")),
    Layer("views/", "视图层：声明式组件，负责画面与键盘事件",
          ("editor/", "line_view", "rendered_line", "sidebar", "table_view")),
    Layer("core/", "核心契约：动作接口、光标状态、撤销栈",
          ("actions", "cursor", "history")),
    Layer("parser/", "解析层：行与段的解析、选区映射、序列化",
          ("_engine", "inline", "block", "reparse", "serialize")),
    Layer("models/", "数据模型：Segment / Line / Document 三级状态",
          ("document",)),
    Layer("services/", "服务层：快捷键、文件读写、备份、导出、Git",
          ("shortcuts", "file_io", "backup", "git/")),
    Layer("utils/", "工具层：文本测量、段与表格工具，不依赖项目内任何模块",
          ("text_layout", "segment_helpers", "table_helpers")),
    Layer("config/ + styles.py", "配置与设计 token：默认设置、配色、排版常量",
          ("settings", "app_meta", "styles")),
)


@dataclass(frozen=True, slots=True)
class Decision:
    title: str
    body: str


ARCH_DECISIONS: tuple[Decision, ...] = (
    Decision(
        "双层 Stack，不做段级拼接",
        "底层画出最终样式，顶层一个全透明输入框接光标。"
        "没有接缝，没有宽度跳动，光标天然落在字中间。",
    ),
    Decision(
        "光标走 ref，画面走 state",
        "打字时只改 ref，不触发重渲染；一次输入结束才同步 state。"
        "否则每敲一个字重排整棵树，光标自己会抖。",
    ),
    Decision(
        "改一行只通知一次",
        "高频路径上合并成一次 observable 通知，而不是逐字段改模型。"
        "大文档跟不跟手，就差在这里。",
    ),
    Decision(
        "只画看得见的那几行",
        "超过 3000 行只构建视口附近的行，滚动范围用留白估算，"
        "不放占位控件，免得量错高度。",
    ),
    Decision(
        "撤销按行存，不存全文",
        "单行编辑只留这一行的原文，内存 O(1)。"
        "换行、合并、多行粘贴这类结构变化才存全文。",
    ),
    Decision(
        "复杂的块自己管自己",
        "代码块、表格、公式、目录各有编辑状态，不挤进段落那套状态机。"
        "它们和段落本来就不是一回事。",
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
    "只能往下依赖。services / parser / models / core / utils 不得反向导入 app 或 views。",
    "界面只由状态驱动，渲染完就不再调 control.update()。",
    "透明光标框不设 value，由 effect 异步清空，否则打断输入法组字。",
    "同一行内输入不换输入框 key，只有撤销、重做这类强制重建才递增会话号。",
    "渲染层与光标层共用同一套断行算法，否则换行点不同、光标必错位。",
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
    TechItem("mistune", ">= 3.3.4", "行内与块级解析、HTML 导出"),
    TechItem("uharfbuzz", ">= 0.40", "文本整形测量，与 Skia 同引擎"),
    TechItem("Pygments", ">= 2.19", "代码分词，未知语言回退纯文本"),
    TechItem("flet-datatable2", "== 1.0.4", "表格渲染与单元格编辑"),
    TechItem("Pillow", ">= 12.3", "读图片尺寸、等比缩放"),
    TechItem("watchdog", ">= 4.0", "监听原文件的外部改动"),
    TechItem("Python", ">= 3.12", "用到 StrEnum 与 PEP 695 语法"),
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
        "Python 3.12 起，一条 pip 命令装完就能启动。",
        (
            DocBlock("p", "依赖和打包配置都在 `pyproject.toml` 里，没有第二个 "
                          "`requirements.txt` 跟着漂。"),
            DocBlock("kv", items=(
                ("Python", "≥ 3.12（用到 StrEnum 与 PEP 695）"),
                ("平台", "Windows 10/11 优先，不需要编译原生扩展"),
                ("依赖", "7 个运行时依赖，都有现成 wheel"),
                ("入口", "`python main.py`，或装好后的 `cs-markdown-editor`"),
            )),
            DocBlock("code", INSTALL_CMD),
            DocBlock("note", "第一次启动会打开内置示例；之后直接给空白页，"
                             "省掉那份 298 行示例的解析时间。"),
        ),
    ),
    DocEntry(
        "docs-start",
        "快速上手",
        "从打开文件夹到导出，五步走完。",
        (
            DocBlock("steps", items=(
                ("打开文件夹", "`Ctrl+Shift+O` 选个目录当工作区，下次还记着。"),
                ("直接写", "点任意一行就有光标；`# ` 开头是标题，`- ` 开头是列表。"),
                ("调格式", "选中按 `Ctrl+B` / `Ctrl+I`，再按一次取消。"),
                ("开多份", "`Ctrl+N` 新标签，`Ctrl+\\` 向右拆一组。"),
                ("存与导出", "`Ctrl+S` 落盘；要给别人看就导出 HTML。"),
            )),
            DocBlock("note", "快捷键在「设置 → 快捷键」里改：点修改、按下新组合键即可，"
                             "和已有键撞车会当场提示。"),
        ),
    ),
    DocEntry(
        "docs-config",
        "配置项",
        "设置面板的五个分区，对应 `settings.json` 里的键。",
        (
            DocBlock("p", "配置存在项目根的 `settings.json`。新版本加了默认键也不会让你报 "
                          "KeyError，`load_settings()` 会和老配置深合并。"),
            DocBlock("kv", items=(
                ("content_max_width", "正文最大宽度（默认 1080）"),
                ("body_font_size / line_height", "字号与行高（默认 16 / 1.6）"),
                ("font_family", "正文字体（默认随包的阿里巴巴普惠体 3.0）"),
                ("zoom", "界面缩放，`Ctrl+Shift+=` 增减"),
                ("word_wrap", "自动换行，对应 `Alt+Z`"),
                ("auto_save / auto_save_interval", "自动保存与间隔（分钟）"),
                ("backup_enabled / backup_interval", "定时备份与间隔（分钟）"),
                ("backup_retention_days", "已命名文档的备份保留天数"),
                ("recover_untitled_days", "未命名草稿保留天数"),
                ("detect_external_changes", "原文件被外部改动时提示重载"),
                ("code_theme_light / _dark", "代码高亮主题"),
                ("git_diff_mode", "差异视图默认模式：unified 或 split"),
                ("log_level", "DEBUG / INFO / WARNING / ERROR"),
            )),
            DocBlock("note", "排查问题时先看两个环境变量：`CS_MD_LOG_DIR` 与 "
                             "`CS_MD_LOG_LEVEL`，它们优先于配置文件。"),
        ),
    ),
    DocEntry(
        "docs-keys",
        "快捷键",
        "51 个动作按用途分组，键位直接从程序里读。",
        (),
    ),
    DocEntry(
        "docs-layout",
        "项目结构",
        "九层，越往下越不知道上层存在，也越好测。",
        (
            DocBlock("code", """cs-markdown-editor/
├── main.py                入口：日志 → 字体 → 主题 → page.render(App)
├── styles.py              配色、段样式、标题字阶、间距 token
├── app/                   应用层：状态容器 + 控制器
│   ├── _render.py         渲染树装配（侧栏 / 标签 / 编辑区 / 状态栏）
│   ├── _tab_management.py 按组管理标签的增删改查
│   ├── _split_editor.py   拆分视图（左右两组独立标签）
│   └── _git_controller.py Git 面板的调度与状态映射
├── views/                 视图层：声明式组件
│   ├── editor/            编辑器组件包（约 20 个工厂模块）
│   ├── line_view.py       行视图（岛屿分支 + 渲染层 + 光标层）
│   ├── pixel_layout.py    像素布局缓存与命中测试
│   └── key_bindings.py    键盘事件分发
├── core/                  动作契约 / 光标状态 / 撤销栈
├── parser/                行与段的解析、重解析、序列化
├── models/                Segment - Line - Document 三级状态
├── services/              快捷键 / 文件 IO / 备份 / 导出 / Git
├── utils/                 文本测量 / 段工具 / 表格工具
├── config/                默认设置与元信息
└── tests/                 1747 条测试 + 进程内渲染夹具"""),
            DocBlock("p", "依赖只能从上往下。`utils/` 不认识项目里任何模块，"
                          "`parser/` 和 `models/` 不知道界面存在，所以能纯函数式地做往返测试。"),
        ),
    ),
    DocEntry(
        "docs-faq",
        "常见问题",
        "性能、数据、平台，几个直白的回答。",
        (
            DocBlock("kv", items=(
                ("大文档会卡吗？",
                 "3000 行以上只画视口附近的行，重绘成本和总行数脱钩。"),
                ("文件会被上传吗？",
                 "不会。本地桌面应用，除了 Git 命令本身不发任何请求。"),
                ("编辑时文件被改了？",
                 "监听原文件时间戳，发现外部改动会问你要不要重载；"
                 "定时备份独立在后台跑。"),
                ("为什么不做成 Web 编辑器？",
                 "像素级光标和输入法行为依赖 Flutter 的渲染管线与本机字体测量，"
                 "浏览器里做不到同样的精度。"),
                ("支持 macOS / Linux 吗？",
                 "核心跑在 Flet 跨平台运行时上，但主要在 Windows 上验证；"
                 "快捷方式解析和部分文件操作只有 Windows 有。"),
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
        "标题、引用、列表、强调，日常写得最多的几种。",
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
        "代码块带高亮和行号，公式块渲染 LaTeX。",
        """## 渲染流程

```python
def reparse_line_atomic(doc, index, raw):
    line = build_line(raw)
    doc.lines[index] = line   # 一次通知，而不是逐字段
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
        "表格的单元格，点一下就能改。",
        """## 依赖一览

| 依赖 | 作用 | 版本 |
| --- | --- | --- |
| flet | 声明式 UI | 1.0.4 |
| mistune | 解析 | 3.3.4 |
| uharfbuzz | 文本测量 | 0.55 |

行高恒定，整段内容放在悬停提示里，
进出编辑态时页面不会跟着跳。
""",
    ),
    DemoSample(
        "任务与引用",
        "任务列表能勾选，引用可以一层层套。",
        """## 发布前检查

- [x] 1747 条测试全绿
- [x] 拆分视图回归
- [ ] 复核打包体积
- [ ] 更新 CHANGELOG

> 第一层引用
> > 第二层引用，左边框会加宽
""",
    ),
)

# ---------------------------------------------------------------------------
# 关于
# ---------------------------------------------------------------------------

ABOUT_PARAGRAPHS: tuple[str, ...] = (
    "做这个编辑器，起因是两件不顺手的事：要不盯着源码猜效果，"
    "要不每段一个输入框，跨段选中、行首退格处处别扭。",
    "所以文档一直是排好版的样子，光标浮在文字上面。代价是文本测量、断行、"
    "点击定位都得自己写，这也是 views/ 目录最大的原因。",
    "依赖只有七个，一层只依赖下一层，1747 条测试盯着行为。"
    "功能慢慢加，别改了一处、坏在另一处。",
)

ABOUT_FACTS: tuple[tuple[str, str], ...] = (
    ("作者", APP_COMPANY),
    ("组织", APP_AUTHOR),
    ("许可", APP_LICENSE),
    ("技术栈", "Python · Flet · mistune · HarfBuzz"),
)

FOOTER_NOTE = "这个介绍页也是 Flet 写的，配色和字体跟编辑器共用一套。"
