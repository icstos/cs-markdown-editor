# Debug: repeat-char-input-glitch

**Status:** [OPEN]
**Session ID:** repeat-char-input-glitch
**Created:** 2026-07-31

## 症状

连续输入6个相同英文字符 `a` 时,输入存在延迟与异常:
- 第1个:a(正常)
- 第2个:显示为"宽度大于空格的空"(异常)
- 第3个:aa(少1个a)
- 第4个:aa + 空(异常)
- 第5个:aa + 两个空(异常)
- 第6个:aaa(3个a)

最终只有3个a,且中间出现不可见的"宽度大于空格的空"。

## 假设

- **A: TextField on_change 的 value 不是累积的**:每次 on_change 的 value 可能是单字符(本次输入),而非完整 TextField value。若为单字符,第2次 value="a"==last_value="a" → 分支1 ignore,吞掉第2个字符。
- **B: _fix_ime_doubling 仍在某些场景误折叠**:尽管修复了 len>=4 的合法连续判断,但可能在其他长度仍误折叠。
- **C: _reparse_atomic 后行 raw 与 TextField value 不同步**:reparse 后行 raw 更新,但 TextField value 仍是旧值,导致下次 on_change 的 value 与行 raw 不匹配,触发错误的分支选择。
- **D: 分支1 ignore 的 last_value.startswith(value) 误判**:当 value 不累积(单字符)时,value="a" 是 last_value="a" 的"前缀"(相等),误触发 ignore。
- **E: cursor_ref.reset 后光标位置与 start_off 不同步**:reset 更新 base/extent,但下次 on_change 时 start_off/end_off 计算基于过时的 cursor_off,导致 new_raw 插入位置错误。

## 插桩点

- `views/editor/_cursor.py` handle_char_input:函数入口(value/raw)、_fix_ime_doubling 返回值、新会话启动、分支选择(ignore/replace/append)、new_raw、last_value 更新

## 证据收集

(待填)
