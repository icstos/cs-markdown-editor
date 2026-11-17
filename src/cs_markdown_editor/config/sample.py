"""内置示例文档：首次启动时展示的 Markdown 样例。

依赖项：无。
对外接口：SAMPLE_MD: str。
"""

SAMPLE_MD: str = r"""Markdown 编辑器

基于 Flet 0.86.2 声明式组件与 mistune 实时渲染，参考 Typora 的段级编辑体验。
长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试长段落测试

## 特性
- 所见即所得

# 测试
### 行内元素

斜体：一级强调：*斜体*
粗体：二级强调：**粗体**
~~删除文本~~
H~2~O
x^2^
==高亮==

常规文本，**加粗**，*斜体*，**加粗*内部*斜体**，***加粗且斜体***,~~删除文本~~，==高亮==
测试，**加粗**，*斜体*，***加粗且斜体***

支持 `行内代码`、**加粗**、*斜体*、~~删除线~~、[链接](https://flet.dev) 与行内公式 $E=mc^2$。
- **加粗**、*斜体*、`行内代码`、~~删除线~~、[链接](https://flet.dev)、$a=b+c$
测试，**加粗**，*斜==体==*，***加粗且斜体***,~~删除文本~~ ==高亮==
测试，**加粗**，*斜体*，***加粗且斜体***
行内代码: `import os`

- 英文效果
This is a **bold** text and this is *italic*. Here's some `inline code`.

- ==高亮==
- 上标：$x^2$
- 下标：$x_3$

### 标题
# 一级标题
## 二级*标题*
### 三级**标题**
#### 四级`标题`
##### 五级标题
###### 六级标题

- 段级编辑：点击任意段即显示其最小语法，其余保持渲染样式
- 三级状态：文档 / 行 / 文本段
- 支持 `代码块`、列表、引用、分隔线
- 水平分割线

链接：[百度](http://www.baidu.com)

$$
x = \dfrac{-b \pm \sqrt{b^2 - 4ac}}{2a}
$$


#### 列表
- 列表、分割线等`- + *`通用的地方建议统一使用`-`，因为它只需按一个键

- 无序**列表1**
- 无序*列表2*
  - 无序列表3
  - 无序列表4
    - 无序列表**5**

1. 有序列表1
2. 有序列表2
    1. 有序列表2-1
    2. 有序列表2-2

使用 `- [ ]` 和 `- [x]` 语法可以创建复选框，实现 todo-list 等功能。例如：

- [ ] 任务列表/复选框1
- [ ] 任务列表/复选框2
- [x] 任务列表/复选框3
    - [x] 任务列表*3-1*

1. 第一步
2. 第二步
   1. 子步骤1
   2. 子步骤2
3. 第三步

嵌套列表
> 这是一段引用文字，左侧有边框、文字柔和。
> 引用，块注释
>
> > 双层引用（嵌套引用）



> 引用 **加粗**
> > 双层引用，**加粗**，**加粗**，*斜体*，***加粗且斜体***


## 图片

- 本地图
![大图](assets/images/big.png)

![百度](https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png)

## 代码块

- python

```python
import os

def greet(name: str) -> str:
    return f"hello, {name}"
```
- xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<o>
     <HelloWorld class="object">
          <中文意思 type="string">你好世界</中文意思>
          <出处 type="string">《The C Programming Language》的第一个演示程序</出处>
          <字符长度 type="number">10</字符长度>
          <相关语言 class="array">
               <e type="string">Python</e>
               <e type="string">C</e>
               <e type="string">C++</e>
               <e type="string">etc</e>
          </相关语言>
     </HelloWorld>
</o>

```

- json

```json
{
    "HelloWorld": {
        "中文意思": "你好世界",
        "出处": "《The C Programming Language》的第一个演示程序",
        "字符长度": 10,
        "相关语言": [
            "Python",
            "C",
            "C++",
            "etc"
        ]
    }
}
```
- yaml

```yaml
name: chen
age: 18
print: HelloWorld
```
- ini

```ini
name: chen
age: 18
print: HelloWorld
```
- 无语言标记的代码块
```
这是没有语言标记的代码块
可以包含任意内容
```

## 表格

| 标题             |       标题       |             标题 |
| :--------------- | :--------------: | ---------------: |
| 居左测试文本     |   居中测试文本   |     居右测试文本 |
| 居左测试文本 1   |  居中测试文本 2  |   居右测试文本 3 |
| 居左测试文本 11  | 居中测试文本 22  |  居右测试文本 33 |
| 居左测试文本 111 | 居中测试文本 222 |  居右测试文本 333 |


| 标题            |      标题       |            标题 |
| :-------------- | :-------------: | --------------: |
| 居左测试文本    |  居中测试文本   |    居右测试文本 |
| 居左测试文本1 长文本段落，居左测试文本1 长文本段落居左测试文本1 长文本段落居左测试文本1 长文本段落居左测试文本1 长文本段落 ，居左测试文本1 长文本段落居左测试文本1 长文本段落居左测试文本1 长文本段落居左测试文本1 长文本段落 |  居中测试文本2  |   居右测试文本3 |
| 居左测试文本11  | 居中测试文本22  |  居右测试文本33 |
| 居左测试文本111 | 居中测试文本222 | 居右测试文本333 |




| 快捷键    | 功能   |
| ------ | ---- |
| Ctrl+B | 粗体   |
| Ctrl+I | 斜体   |
| Ctrl+K | 链接   |
| Ctrl+S | 手动保存 |
| Ctrl+/ | 原文模式   |


## 目录
[toc]

## 图片

![网络图像](https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png)

![百度首页图](Markdown格式渲染效果测试.assets/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png)

```markdown
![图片名](http://图片地址)
![Alt text](/path/to/img.jpg "Optional title")
```

## 分割线
---

```markdown
以下三者均可，推荐 ---
***
---
+++ 
```


## 脚注（footnote）

hello[^1]

[^1]: hi

```markdown
hello[^1]
[^1]: hi
```

# LaTeX 公式
> LaTeX：For数学公式，
> 公式：equation：$ a+b=c$，此时表现为数学模式，所有的字母表示为分离的变量，字母的间距会变大
> 行内公式：inline equation，一些简单的公式，可以放在行内，如$\Gamma(n) = (n-1)!\quad\forall n\in\mathbb N$。
> 行间公式：displayed equation，如求和、求积分较大或内容较复杂，不适合放在行间
> 函数名，需罗马正体，采用反斜杠+函数名来实现。比如 \sin，\cos，\log，\ln。
> 算子，如 \lim，\max、\min、\gcd 等。
- 简单的多个公式堆积：
    - gather：公式居中
    - align：可用 `&` 符号指定位置对齐，如按等号对齐。
$$	x = \dfrac{-b \pm \sqrt{b^2 - 4ac}}{2a} $$
$$
\mathbf{V}_1 \times \mathbf{V}_2 =  \begin{vmatrix}
\mathbf{i} & \mathbf{j} & \mathbf{k} \\
\frac{\partial X}{\partial u} &  \frac{\partial Y}{\partial u} & 0 \\
\frac{\partial X}{\partial v} &  \frac{\partial Y}{\partial v} & 0 \\
\end{vmatrix}
$$


## 运算
- 加减：`\pm` $\pm$
- 乘：`\times` $\times$
- 除：`\div` $\div$
- 点乘：`\cdot` $\cdot$
- 小于等于：`\leq` $\leq$
- 大于等于： `\geq` $\geq$
- 不等于：`\neq` $\neq$
- 约等于：`\approx` $\approx$
- 恒等于：`\equiv` $\equiv$
- 空格：`\quad` $a \quad b$
- 两空格：`\qquad` $a \qquad b$
- 大空格：`a\ b ` $a \ b$
- 中空格：`a \; b` $a\;b$
- 小空格：`\,` $a\,b$

## 上下标
- 下标：`x_2` $x_2$
- 上标：`x^2` $x^2$
- 向量 Vectors： `\vec{a}` $\vec {a}$
- 度：`37^{\circ}` $37^{\circ}$
- 上划线：`\overline{aa}` $\overline{m+n}$
- 下划线：`\underline{aa}` $\underline{m+n}$
- 下方大括号 `\underbrace{a+b+\cdots+z}_{26}`  $\underbrace{a+b+\cdots+z}_{26}$
- 上方大括号 `\overbrace{a+b+\cdots+z}^{26}`  $\overbrace{a+b+\cdots+z}^{26}$
- 分数 fraction：`\frac{}{}` $\frac{1}{2}$
- 根式：`\sqrt{x}`：$\sqrt{x}$
  - `\sqrt [3]{2}` $\sqrt[3] {2}$

## 高等数学
- 行列式： `\begin{vmatrix} 1&2&3\\4&5&6\end{vmatrix}` $\begin{vmatrix} 1&2&3\\4&5&6\end{vmatrix}$
- 矩阵：`\begin{bmatrix} 1&2&3\\4&5&6 \end{bmatrix}` $\begin{bmatrix} 1&2&3\\4&5&6 \end{bmatrix}$
  - `\begin{matrix} 1&2&3\\4&5&6 \end{matrix}` $\begin{matrix} 1&2&3\\4&5&6 \end{matrix}$
  - matrix：无
  - bmatrix：方括号
  - vmatrix：竖线
  - pmatrix：圆括号
  - Bmatrix：花括号
  - Vmatrix：双竖线
- 大括号：`\begin{Bmatrix} y_1&=&ax_1+bx_2 \\ y_2&=&cx_1+dx_2\end{Bmatrix}` $\begin{Bmatrix} y_1&=&ax_1+bx_2 \\ y_2&=&cx_1+dx_2\end{Bmatrix}$
- 属于：`\in` $\in$
- 求和sum： `\sum_{i=1}^{n}`  $\sum_{i=1}^{n}$
- 积分integral： `\int_{0}^{\frac{\pi}{2}} ` $\int_{0}^{\frac{\pi}{2}} $
- 乘积 product：`\prod` $\prod_ \epsilon$
- 模：`\left \| x \right \|` $\left \| x \right \|$  $||x||$
- 求偏导：`\partial L(x,y)`  $\partial L(x,y)$

- 普朗克常数，\hbar
- 无穷符号，\infty
- 空集符号，\emptyset（也可以调用 amssymb 宏包后使用 \varnothing）
- 偏微分符号，\partial
- 积分符号，\int，\iint，\iiint，\iiiint，分别对应一重、二重、三重、四重积分；更多重积分可以用 \idotsint
- 环路积分符号，\oint
- 求和符号，\sum
- 求积符号，\prod
- 交集符号，\cap；并集符号，\cup
- 乘号，\times；除号，\div
- 不等号，\neq；小于等于，\leq；大于等于，\geq；
- 属于，\in；

## 括号


## 集合

`higher order roots:\sqrt[3]{2}` $\sqrt[3]{2}$
`root sign:\surd[x+y]` $\surd[x+y]$
`factions:\frac{a+b}{a-\frac{a}{b}}` $\frac{a+b}{a-\frac{a}{b}}$ 
`force large(display)faction:\dfrac{a+b}{a-\dfrac{a}{b}}`$\dfrac{a+b}{a-\dfrac{a}{b}}$
`continued fraction:1+\cfrac{2}{3+\cfrac{4}{5+\cfrac{6}{7+\dotsb}}}=\frac{1}{\sqrt{e}-1}`
$1+\cfrac{2}{3+\cfrac{4}{5+\cfrac{6}{7+\dotsb}}}=\frac{1}{\sqrt{e}-1}$
`binomal:\binom{n+1}{k}` $\binom{n+1}{k}$
`prime:y''+y'=y` $y''+y'=y$


## 希腊字母
- `\alpha` $\alpha$
- `\beta` $\beta$
- `\gamma` $\gamma$
  - `\Gamma` $\Gamma$
- `\delta` $\delta$
  - `\Delta` $\Delta$
- `\epsilon` $\epsilon$
  - `\varepsilon`  $\varepsilon$ ??
- `\zeta` $\zeta$
- `\eta` $\eta$
- `\theta` $\theta$
  - `\Theta` $\Theta$
- `\iota`$\iota$ 
- `\kappa` $\kappa$
- `\lambda`  $\lambda$
  - `\Lambda`  $\Lambda$
- `\nu` $\nu$
- `\mu` $\mu$
- `\xi` ：$\xi$
- `\pi`：$\pi$
  - `\Pi`：$\Pi$
- `\rho` $\rho$
- `\sigma` $\sigma$
  - `\Sigma`：$\Sigma$
- `tau`：$\tau$
- `\upsilon` ：$\upsilon$
  - `\Upsilon`：$\Upsilon$
- `\phi` $\phi$
  - `\Phi`$\Phi$
- `\chi` $\chi$
- `\psi` $\psi$
  - `\Psi` $\Psi$
- `\omega` $\omega$
  - `\Omega`$\Omega$ 
`\varphi` $\varphi$
"""
