# 高校安全准入考试 · 自动答题工作流

把"拉卷 → AI 出答案 → 提交 → 筛选错题 → 修正"整条流程自动化，适配校内安全准入考试系统（oberyun 平台，multipart saveAnswer 接口即时判分）。

> ⚠️ 仅供本人账号/本人的考试练习使用。敏感信息（token、cookie、paperId、API Key）一律放 `.env`（已 gitignore），仓库内只保留占位符。

## 工作流

```
拉卷 testPaper → AI 生成答案(openai 库占位) → saveAnswer 逐题提交
   → 读取返回 res(right/wrong) 筛选错题 → 错题重新喂给 AI 修正 → 再提交（最多 N 轮）
```

## 快速开始

```bash
python -m pip install requests openai

# 配置
cp .env.example .env     # 填入 token / cookie / paperId / OPENAI_API_KEY

# 运行
python 自动答题工作流.py             # 完整流程
python 自动答题工作流.py --rounds 5  # 错题最多修正 5 轮
python 自动答题工作流.py --no-submit # 只拉卷 + AI 出答案，不提交
```

## 关键口径（本平台实测）

| 项 | 值 |
|---|---|
| 拉卷接口 | `GET /theory/thePaper/testPaper` |
| 提交接口 | `POST /theory/thePaper/saveAnswer`（multipart） |
| 判分字段 | 返回体 `result.res`：`right` / `wrong` |
| 判断题 | **正确=1，错误=0**（错误不是 2，本卷最大坑点） |
| 单选题 | 选项字母 `A/B/C/D` |
| 多选题 | 连续字母无分隔符，如 `ABCD`、`BD` |

## 文件

- `自动答题工作流.py` — 主流程脚本（openai 库占位 AI 答题）
- `提示词.md` — AI 答题系统提示词 + 错题修正提示词
- `工作流说明.md` — 完整流程与实现细节
- `.env.example` — 环境变量模板
