# 版权与许可

**Copyright and licenses** · [English](#english)

这个仓库包含两样东西：问问立正的程序，以及它回答时检索的资料副本。两者的许可不同。

| 部分 | 许可 |
|---|---|
| 程序和文档：`server/` `src/` `scripts/` `tests/` `docs/`，以及其他没有另外说明的文件 | [MIT](LICENSES/MIT.txt) |
| `data/context/`：[立正 Open Context](https://github.com/sunyuzheng/lizheng-open-context) 的哈希校验副本 | 每个文件沿用 Open Context 的许可，见 [`data/context/LICENSE.md`](data/context/LICENSE.md)：立正的文字是 CC BY 4.0；《真本事》课程文字稿和会员视频对话字幕是立正参考使用许可；其他作者的作品保留原作者的权利；目录数据是 CC0 |
| `data/semantic/`：从 `data/context/` 的文字算出的检索向量 | 跟随对应原文的许可，只用于检索 |

MIT 许可只覆盖程序和文档，不覆盖 `data/` 里的资料。「立正」「问问立正」「超线性学院」等名称和标识也不在任何许可之内；基于本仓库做的产品不能暗示是立正官方出品或得到他的认可。

---

## English

This repository contains two things: the Ask Lizheng application, and a copy of the material it searches when answering. They are licensed differently.

| Part | License |
|---|---|
| Code and documentation: `server/`, `src/`, `scripts/`, `tests/`, `docs/`, and any other file not stated otherwise | [MIT](LICENSES/MIT.txt) |
| `data/context/`: a hash-verified copy of [Lizheng Open Context](https://github.com/sunyuzheng/lizheng-open-context) | Each file keeps its Open Context license; see [`data/context/LICENSE.md`](data/context/LICENSE.md). Yuzheng Sun's writing is CC BY 4.0; the *真本事* course texts and member video conversation transcripts are under the Lizheng Reference Use License; other authors' work keeps its original rights; catalog data is CC0 |
| `data/semantic/`: search vectors computed from the text in `data/context/` | Follows the license of the source text; for retrieval only |

The MIT license covers only the code and documentation, not the material in `data/`. The names and marks 立正, 问问立正 (Ask Lizheng), and 超线性学院 (Superlinear Academy) are not licensed; products built from this repository must not suggest that they are official or endorsed by Yuzheng Sun.
