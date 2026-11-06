# 02 来源与权威规则

对应流程第 2 步「知识与权威」。

## 来源清单

2026-11-03 起只入库下表这一条。英文规则书和中文 5.2 取消，本地文件保留，不解析进索引。

| 编号 | 来源 | 规则版本 | 语言 | 格式 | 许可证 | 角色 |
| --- | --- | --- | --- | --- | --- | --- |
| `src-srd51-zh` | SagiriWWW/DND.SRD.zh-CN | 5.1 | 中文 | Markdown | 未声明 | 唯一收录（机翻） |

已取消、不入库：

| 编号 | 来源 | 取消原因 |
| --- | --- | --- |
| `src-srd51-en` | 威世智官方 SRD 5.1 PDF | 2026-11-03 取消全部英文规则书 |
| `src-srd521-en` | 威世智官方 SRD 5.2.1 PDF | 2026-11-03 取消全部英文规则书 |
| `src-srd52-zh` | DND5eChm/SRD5.2Chm | 2026-11-03 取消中文 5.2 |

获取地址：

- `src-srd51-en`：<https://media.wizards.com/2023/downloads/dnd/SRD_CC_v5.1.pdf>
- `src-srd521-en`：<https://www.dndbeyond.com/srd>（页面列出 English SRD v5.2.1，发布于 2025-05-01）
- `src-srd52-zh`：<https://github.com/DND5eChm/SRD5.2Chm>，使用目录 `玩家手册2024`、`城主指南2024`、`怪物图鉴2025`
- `src-srd51-zh`：<https://github.com/SagiriWWW/DND.SRD.zh-CN>，使用 `docs` 目录

入库只固定 `src-srd51-zh` 的提交号。上游变更不会自动进入索引。下面三行是已取消来源的本地记录，不入库。

当前本地已下载的版本（2026-10-16 登记）：

| 编号 | 本地路径 | 固定值 |
| --- | --- | --- |
| `src-srd51-en` | `SRD_CC_v5.1.pdf` | SHA-256 `2504D2A0ABB0A4D491A939BE4F17910A2DDE0312570AB8D208080225CCF0A1F0` |
| `src-srd521-en` | `SRD_CC_v5.2.1.pdf` | SHA-256 `8974902D109D6E63672D7C490BDE9CCF052410503D9CFA768237154FBC5E3D87` |
| `src-srd52-zh` | `SRD5.2Chm/` | 提交 `dec9e054585905b40ab66a12cb12ab13416e822d` |
| `src-srd51-zh` | `DND.SRD.zh-CN/` | 提交 `9b711c46d93dbf1d70a9bdf72703e0ecafd50ca8` |

## 权威规则

1. 入库和回答都只用中文 5.1。数值以该译文为准，不再并列英文原文。
2. 不与英文规则书对齐。条目编号仍用 Markdown 文件名里的英文条目名，例如 `Spells/Fireball.md` 编为 `srd51:spell:fireball`。
3. 不入库 5.2。问到 2024 规则或两版差异时拒答。
4. 术语以中文 5.1 的条目标题为准。
5. `src-srd51-zh` 的译文在展示时必须标注「机器翻译，非官方」。

## 新鲜度时限

SRD 很少变动，时限按变动类型分别规定：

| 变动 | 必须反映到回答里的时限 |
| --- | --- |
| 中文 5.1 上游勘误或换提交 | 30 天内（经审核后接入） |
| 收到下架请求 | 7 天内从索引和缓存中移除 |
| 发现入库错误（解析错误、切分错误） | 7 天内 |

检查方式：每月人工查看一次 `src-srd51-zh` 上游仓库的提交记录。第二期改为自动检查，有变化时告警。

## 数据量（入库后以实测为准）

| 来源 | 估计规模 | 是否入库 |
| --- | --- | --- |
| `src-srd51-zh` | 约 1 MB | 是 |
| `src-srd51-en` | 约 400 页 | 否 |
| `src-srd521-en` | 约 360 页 | 否 |
| `src-srd52-zh` | 约 5 MB | 否 |

只统计 `src-srd51-zh`。数据量基本不增长，只在这份译文更新时整体变化。

## 密级与出域规则

- 收录的来源是公开资料，不含个人信息，密级为「公开」。已取消的三个来源同样不入库。
- 用户提问可能偶尔含有个人信息，按低敏感处理：日志只保留必要字段，账号标识做假名化，见 [08-operations.md](08-operations.md)。
- 出域：允许把提问和知识片段发送给云端模型服务，包括国内和海外的服务商。
- 部署在香港或海外，不需要 ICP 备案。如果将来改为在中国大陆部署，需要重新评估 ICP 备案和生成式 AI 服务备案或登记的要求，具体以监管部门的规定为准，需要核实。

## 署名与许可

CC-BY-4.0 的核心义务是署名。系统的「关于」页面和每个引用卡片只带 `src-srd51-zh` 的署名。已取消来源的署名原文留在下面，不展示。

`src-srd51-en` 的署名，按 PDF 法律信息页的原文：

> This work includes material taken from the System Reference Document 5.1 ("SRD 5.1") by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.

`src-srd521-en` 的署名，按 PDF 第 1 页法律信息中要求照抄的那段，2026-10-24 核对：

> This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

两份官方 PDF 都要求不加其他关于威世智的署名，但允许注明「compatible with fifth edition」或「5E compatible」。

`src-srd52-zh` 的署名：注明译文来自 DND5eChm/SRD5.2Chm，附仓库链接，并说明本系统对其做了格式转换和分块。`法律信息.htm` 已于 2026-10-24 阅读。源文件从 `("Wizards")` 起句，开头半句缺失，没有另写中文署名义务。其中要求照抄的是 SRD 5.2 的署名，不是 5.2.1：

> This work includes material from the System Reference Document 5.2 (“SRD 5.2”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

这两段署名留作记录，不在「关于」页和引用卡片展示。不把 5.2 改写成 5.2.1。

`src-srd51-zh` 的署名：注明译文来自 SagiriWWW/DND.SRD.zh-CN，上游为 OldManUmby/DND.SRD.Wiki，内容为机器翻译、非官方。2026-10-24 读过 `docs/NOTICE.md` 和 `docs/Legal.md`：上游说明 SRD 5.1 原文是 CC-BY-4.0，`Legal.md` 只是 5.1 署名的中文转述，没有把译文本身授权出去。再分发风险和 7 天内下架的应对不变。

## 已知风险

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| `src-srd51-zh` 未声明许可证 | 译文的再分发权利不明确；非商业用途不能豁免版权 | 署名、标注机翻；收到下架请求后 7 天内整体移除 |
| `src-srd51-zh` 是机翻 | 作者声明未经完整逐条人工校对，且不再用英文书核对 | 标注机翻；抽查中文条目的标题、数值和表格 |

## 下架流程

1. 收到下架请求后，管理员确认请求方和涉及的来源编号。
2. 把该来源标记为「停用」，在线检索立即不再返回它的条目。
3. 删除该来源的块、向量和缓存，重建受影响的索引版本。
4. 用该来源的典型问题做一次检索，确认不再返回它的内容。
5. 在来源清单里记录下架日期和原因。

从收到请求到第 4 步完成，不超过 7 天。
