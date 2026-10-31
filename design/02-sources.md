# 02 来源与权威规则

对应流程第 2 步「知识与权威」。

## 来源清单

| 编号 | 来源 | 规则版本 | 语言 | 格式 | 许可证 | 角色 |
| --- | --- | --- | --- | --- | --- | --- |
| `src-srd51-en` | 威世智官方 SRD 5.1 | 5.1 | 英文 | PDF | CC-BY-4.0 | 权威 |
| `src-srd521-en` | 威世智官方 SRD 5.2.1 | 5.2 | 英文 | PDF | CC-BY-4.0 | 权威 |
| `src-srd52-zh` | DND5eChm/SRD5.2Chm | 5.2 | 中文 | HTML（GBK 编码） | CC-BY-4.0 | 辅助 |
| `src-srd51-zh` | SagiriWWW/DND.SRD.zh-CN | 5.1 | 中文 | Markdown | 未声明 | 辅助（机翻） |

获取地址：

- `src-srd51-en`：<https://media.wizards.com/2023/downloads/dnd/SRD_CC_v5.1.pdf>
- `src-srd521-en`：<https://www.dndbeyond.com/srd>（页面列出 English SRD v5.2.1，发布于 2025-05-01）
- `src-srd52-zh`：<https://github.com/DND5eChm/SRD5.2Chm>，使用目录 `玩家手册2024`、`城主指南2024`、`怪物图鉴2025`
- `src-srd51-zh`：<https://github.com/SagiriWWW/DND.SRD.zh-CN>，使用 `docs` 目录

每个来源入库时都要固定到一个不可变的版本：PDF 记录 SHA-256 校验值，Git 仓库记录提交号。只接入固定后的版本，上游的变更不会自动进入索引。

当前本地已下载的版本（2026-10-16 登记）：

| 编号 | 本地路径 | 固定值 |
| --- | --- | --- |
| `src-srd51-en` | `SRD_CC_v5.1.pdf` | SHA-256 `2504D2A0ABB0A4D491A939BE4F17910A2DDE0312570AB8D208080225CCF0A1F0` |
| `src-srd521-en` | `SRD_CC_v5.2.1.pdf` | SHA-256 `8974902D109D6E63672D7C490BDE9CCF052410503D9CFA768237154FBC5E3D87` |
| `src-srd52-zh` | `SRD5.2Chm/` | 提交 `dec9e054585905b40ab66a12cb12ab13416e822d` |
| `src-srd51-zh` | `DND.SRD.zh-CN/` | 提交 `9b711c46d93dbf1d70a9bdf72703e0ecafd50ca8` |

## 权威规则

1. 英文官方 PDF 是事实来源。中文译文和英文冲突时以英文为准，回答中同时附上英文原文。
2. 中文条目必须对应到一条英文 SRD 条目才能入库，对应不上的丢弃，并记入对齐失败清单。这条规则同时挡住了混进中文仓库的非 SRD 内容。
3. 5.1 和 5.2 是两套独立的规则，不互相覆盖。同名条目分别入库，通过概念键关联（见 [07-pipelines.md](07-pipelines.md)）。
4. 术语译名以 `src-srd52-zh` 为准，统一维护在术语表中。5.1 独有的术语由模型初译，审核员审核后补入术语表。
5. `src-srd51-zh` 的译文在展示时必须标注「机器翻译，非官方」。

## 新鲜度时限

SRD 很少变动，时限按变动类型分别规定：

| 变动 | 必须反映到回答里的时限 |
| --- | --- |
| 官方发布新版本或勘误版 | 30 天内 |
| 中文辅助来源上游更新 | 30 天内（经审核后接入） |
| 收到下架请求 | 7 天内从索引和缓存中移除 |
| 发现入库错误（解析错误、对齐错误） | 7 天内 |

检查方式：每月人工查看一次官方 SRD 页面和两个上游仓库的提交记录。第二期改为自动检查，有变化时告警。

## 数据量（入库后以实测为准）

| 来源 | 估计规模 |
| --- | --- |
| `src-srd51-en` | 约 400 页 |
| `src-srd521-en` | 约 360 页 |
| `src-srd52-zh` | 约 5 MB（按仓库体积估计，含 PDF 与样式文件） |
| `src-srd51-zh` | 约 1 MB |

四个来源合计估计几千个条目、一至两万个块。数据量基本不增长，只在新版本发布时整体变化。

## 密级与出域规则

- 四个来源全部是公开资料，不含个人信息，密级为「公开」。
- 用户提问可能偶尔含有个人信息，按低敏感处理：日志只保留必要字段，账号标识做假名化，见 [08-operations.md](08-operations.md)。
- 出域：允许把提问和知识片段发送给云端模型服务，包括国内和海外的服务商。
- 部署在香港或海外，不需要 ICP 备案。如果将来改为在中国大陆部署，需要重新评估 ICP 备案和生成式 AI 服务备案或登记的要求，具体以监管部门的规定为准，需要核实。

## 署名与许可

CC-BY-4.0 的核心义务是署名。系统的「关于」页面和每个引用卡片都必须带上对应的署名。

`src-srd51-en` 的署名，按 PDF 法律信息页的原文：

> This work includes material taken from the System Reference Document 5.1 ("SRD 5.1") by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.

`src-srd521-en` 的署名，按 PDF 第 1 页法律信息中要求照抄的那段，2026-10-24 核对：

> This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

两份官方 PDF 都要求不加其他关于威世智的署名，但允许注明「compatible with fifth edition」或「5E compatible」。

`src-srd52-zh` 的署名：注明译文来自 DND5eChm/SRD5.2Chm，附仓库链接，并说明本系统对其做了格式转换和分块。`法律信息.htm` 已于 2026-10-24 阅读。源文件从 `("Wizards")` 起句，开头半句缺失，没有另写中文署名义务。其中要求照抄的是 SRD 5.2 的署名，不是 5.2.1：

> This work includes material from the System Reference Document 5.2 (“SRD 5.2”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

使用这个中文来源时带上这段；使用官方 5.2.1 PDF 时带上上一段。两段都保留，不把 5.2 改写成 5.2.1。

`src-srd51-zh` 的署名：注明译文来自 SagiriWWW/DND.SRD.zh-CN，上游为 OldManUmby/DND.SRD.Wiki，内容为机器翻译、非官方。2026-10-24 读过 `docs/NOTICE.md` 和 `docs/Legal.md`：上游说明 SRD 5.1 原文是 CC-BY-4.0，`Legal.md` 只是 5.1 署名的中文转述，没有把译文本身授权出去。再分发风险和 7 天内下架的应对不变。

## 已知风险

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| `src-srd51-zh` 未声明许可证 | 译文的再分发权利不明确；非商业用途不能豁免版权 | 署名、标注机翻；收到下架请求后 7 天内整体移除 |
| `src-srd51-zh` 是机翻 | 作者声明未经完整逐条人工校对 | 以英文为准，回答里并列英文原文 |
| 中文 5.2 与官方 5.2.1 有差异 | 中文译本对应 SRD 5.2，官方最新为 5.2.1 | 以 5.2.1 英文为准；差异进入审核队列 |
| 中文 5.2 的 HTML 编码 | 上游项目要求 GBK 编码，解码错误会产生乱码 | 入库时按 GBK 解码，并检查乱码比例 |

## 下架流程

1. 收到下架请求后，管理员确认请求方和涉及的来源编号。
2. 把该来源标记为「停用」，在线检索立即不再返回它的条目。
3. 删除该来源的块、向量和缓存，重建受影响的索引版本。
4. 用该来源的典型问题做一次检索，确认不再返回它的内容。
5. 在来源清单里记录下架日期和原因。

从收到请求到第 4 步完成，不超过 7 天。
