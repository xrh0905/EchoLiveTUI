# Reused message processing

`echolivetui/message.py` and its regression tests are adapted from
[xrh0905/echo-client](https://github.com/xrh0905/echo-client), commit
`deb2f07d3dc1e422c495966e912be9fea3a8ed78` (local reference supplied by the project owner).
They preserve the original shortcodes, Markdown rendering, pinyin/zhuyin and
pause semantics. The networking, settings and terminal application are new.
