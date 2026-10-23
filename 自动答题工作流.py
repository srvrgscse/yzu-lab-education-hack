# -*- coding: utf-8 -*-
"""
安全准入考试 · 自动答题工作流
================================================================
流程：
  1. 拉取试卷      GET  /theory/thePaper/testPaper
  2. AI 生成答案   使用 OpenAI 库（占位，可换任意 OpenAI 兼容接口）
  3. 提交答案      POST /theory/thePaper/saveAnswer (multipart/form-data)
  4. 读取每次提交返回的 res，筛选 Res=wrong 的题目
  5. 对错题重新让 AI 分析并修正，再提交，直到全对或达到最大轮次

答案格式（本平台实测确认）：
  判断题：正确=1，错误=0（注意：不是 2）
  单选题：选项字母 A/B/C/D
  多选题：连续字母无逗号，如 ABCD、BD

依赖安装：python -m pip install requests openai
敏感参数：token / cookie / paperId / OpenAI Key 一律放在脚本同目录的 .env 文件中
（脚本内仅保留 <占位符>，运行前请先配置 .env，模板见随附 .env 文件）
运行示例：
  python 自动答题工作流.py                  # 完整流程（拉卷→AI→提交→修正）
  python 自动答题工作流.py --rounds 3       # 最多 3 轮修正
  python 自动答题工作流.py --no-submit      # 只拉卷并让 AI 出答案，不提交
================================================================
"""

import argparse
import json
import os
import re
import sys
import time

import requests

# ============================ 1. 配置区 ============================
def load_dotenv(path=".env"):
    """极简 .env 加载器：KEY=VALUE；忽略 # 注释与空行；值可带引号。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)


load_dotenv()  # 读取脚本同目录 .env；敏感信息全部放 .env，本脚本只留占位

# ---- 全部敏感/私有参数均为占位，真实值在 .env 中配置 ----
BASE_URL = os.getenv("BASE_URL", "http://10.191.16.53/oberyun-server/smltplat/theory/thePaper")
STUDENT_PAPER_ID = os.getenv("STUDENT_PAPER_ID", "<yourStudentPaperId>")      # 占位
REFER_OBJECT_TYPE = os.getenv("REFER_OBJECT_TYPE", "<referObjectType>")        # 占位
REFER_OBJECT_ID = os.getenv("REFER_OBJECT_ID", "<referObjectId>")              # 占位
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "<yourAccessToken>")              # 占位
COOKIE = os.getenv("COOKIE", "")                                               # 占位

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US",
    "Origin": "http://10.191.16.53",
    "Referer": (
        f"http://10.191.16.53/yzdxaqpt/smltplat/theory/texam/testPaper"
        f"?theStudentPaperId={STUDENT_PAPER_ID}&thePaperAllotId&"
        f"referObjectType={REFER_OBJECT_TYPE}&referObjectId={REFER_OBJECT_ID}"
        f"&trainPhaseContentFlag="
    ),
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "X-Access-Token": X_ACCESS_TOKEN,
    "Cookie": COOKIE,  # 部分接口可能需要；主鉴权仍为 X-Access-Token
}

# ---- OpenAI 配置（占位）----
# 说明：这是"AI 答题"环节的占位实现。填入你的 API Key 与 base_url 即可使用；
# 兼容 OpenAI 官方接口及一切 OpenAI 兼容网关（如 DeepSeek、通义、本地 vLLM 等）。
from openai import OpenAI  # noqa: E402

AI_CLIENT = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY", "sk-REPLACE-ME-YOUR-KEY"),  # 占位，请替换
    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
)
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ============================ 2. 提示词 ============================
SYSTEM_PROMPT = """你是一名高校实验室安全准入考试的答题助手，负责根据题目内容与选项给出正确答案。

题目类型：判断题（正确/错误）、单选题、多选题。
答题与输出要求：
1. 判断题：正确填 1，错误填 0（不要填 2，不要填 A/B）。
2. 单选题：只填一个选项字母（如 A、B、C、D），不要填选项内容。
3. 多选题：填全部正确选项的字母，连续书写、不加分隔符（如 ABC、ABD），既不能漏选也不能多选。
4. 输出格式：只输出一个 JSON 数组，不要任何解释、注释、前言或 Markdown 代码块标记。
   每个元素格式：[{"serialNumber": 1, "answer": "1"}]
5. 答题依据：用电安全、消防安全、通识安全、制度法规、实验室安全规范等常识；把握不准时按"最稳妥、最安全"的做法判断；严禁编造题目中不存在的选项。"""


def build_user_prompt(questions, wrong_context=None):
    """把题目拼成给 AI 的输入。wrong_context 用于错题修正轮次。"""
    parts = ["请回答以下实验室安全考试题目，按要求的 JSON 数组格式输出：", ""]
    for q in questions:
        parts.append(f"题号 {q['serialNumber']} | 类型：{q['typeName']} | 知识点：{q.get('knowledge', '')}")
        parts.append(f"题目：{q['content']}")
        if q.get("options"):
            letters = "ABCDEFGH"
            opts = [o for o in str(q["options"]).split(";") if o]
            opt_lines = "；".join(f"{letters[i]}、{o}" for i, o in enumerate(opts))
            parts.append(f"选项：{opt_lines}")
        if wrong_context and q["serialNumber"] in wrong_context:
            w = wrong_context[q["serialNumber"]]
            parts.append(f"【注意】你上一次的答案 [{w['answer']}] 被系统判为错误，请重新分析并给出新的答案。")
        parts.append("")
    return "\n".join(parts)


FIX_PROMPT = """你之前的答案被系统判为错误。请重新审题并修正：
- 重点检查：判断题的"错误"必须填 0（"正确"填 1）；单选题字母是否与选项对应；多选题是否恰好覆盖全部正确项。
- 多选题常见失分点：漏选（少一个正确项）或多选（多勾了错误项），请逐项推敲每个选项的对错。
- 仍然只输出 JSON 数组，格式同前：[{"serialNumber": N, "answer": "..."}]。"""


# ============================ 3. 平台接口 ============================
def fetch_paper():
    """拉取试卷，返回题目列表。"""
    url = f"{BASE_URL}/testPaper?_t={int(time.time() * 1000)}&theStudentPaperId={STUDENT_PAPER_ID}&referObjectType={REFER_OBJECT_TYPE}&referObjectId={REFER_OBJECT_ID}&trainPhaseContentFlag="
    resp = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"拉卷失败: {data.get('message')}")
    questions = []
    for item in data["result"]["studentPaperContents"]:
        questions.append({
            "serialNumber": int(item["serialNumber"]),
            "id": item["id"],
            "typeName": item["typeName"],
            "knowledge": item["knowledge"],
            "content": item["content"],
            "options": item.get("options", ""),
        })
    paper = data["result"]["studentPaper"]
    print(f"拉卷成功：{paper['paperName']} ｜ 学生 {paper['realname']} ｜ 状态 {paper['state']} ｜ 共 {len(questions)} 题")
    return questions


def submit_answer(question_id, answer):
    """提交单个答案，返回 {'res': 'right'/'wrong', 'mark': ...}。"""
    payload = [{"id": question_id, "yourAnswer": answer, "mark": None}]
    # requests 的 multipart 编码：字段名 answers / theStudentPaperId
    resp = requests.post(
        f"{BASE_URL}/saveAnswer",
        headers=HEADERS,
        files={
            "answers": (None, json.dumps(payload, ensure_ascii=False)),
            "theStudentPaperId": (None, STUDENT_PAPER_ID),
        },
        verify=False,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"提交失败: {data.get('message')}")
    return data["result"]


# ============================ 4. AI 答题 ============================
def parse_ai_reply(text, questions):
    """从 AI 回复中解析出 {serialNumber: answer}，容错处理代码块与前后缀。"""
    text = text.strip()
    # 去掉可能的 ```json ... ``` 包裹
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if m:
        text = m.group(1)
    # 截取第一个 [ 到最后一个 ]
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1:
        raise ValueError(f"AI 输出无法解析为 JSON 数组：{text[:300]}")
    items = json.loads(text[s:e + 1])
    result = {}
    for it in items:
        result[int(it["serialNumber"])] = str(it["answer"]).strip()
    # 校验覆盖
    missing = [q["serialNumber"] for q in questions if q["serialNumber"] not in result]
    if missing:
        raise ValueError(f"AI 漏答题目：{missing}")
    return result


def ask_ai(questions, wrong_context=None):
    """调用 OpenAI 库生成答案。返回 {serialNumber: answer}。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": build_user_prompt(questions, wrong_context)})
    if wrong_context:
        messages.append({"role": "user", "content": FIX_PROMPT})
    chat = AI_CLIENT.chat.completions.create(
        model=AI_MODEL,
        messages=messages,
        temperature=0.1,
    )
    return parse_ai_reply(chat.choices[0].message.content, questions)


# ============================ 5. 主流程 ============================
def main():
    parser = argparse.ArgumentParser(description="安全准入考试自动答题工作流")
    parser.add_argument("--no-submit", action="store_true", help="只拉卷并让 AI 出答案，不提交")
    parser.add_argument("--rounds", type=int, default=3, help="错题修正最大轮次（默认 3）")
    parser.add_argument("--paper-json", default=None, help="使用本地保存的试卷 JSON 文件，跳过拉卷")
    args = parser.parse_args()

    # ---- 1. 拉卷 ----
    if args.paper_json:
        with open(args.paper_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        questions = []
        for item in data["result"]["studentPaperContents"]:
            questions.append({
                "serialNumber": int(item["serialNumber"]),
                "id": item["id"],
                "typeName": item["typeName"],
                "knowledge": item["knowledge"],
                "content": item["content"],
                "options": item.get("options", ""),
            })
        print(f"从本地文件载入试卷：共 {len(questions)} 题")
    else:
        questions = fetch_paper()

    # ---- 2. AI 生成答案 ----
    print("\n[1/3] AI 生成答案 ...")
    answers = ask_ai(questions)
    print(f"AI 返回 {len(answers)} 个答案")

    if args.no_submit:
        print("\n[--no-submit] 未提交，答案预览：")
        for q in questions:
            print(f"  #{q['serialNumber']} {q['typeName']}：{answers[q['serialNumber']]}")
        return

    # ---- 3. 提交 + 4. 筛选 Res=wrong ----
    print("\n[2/3] 逐题提交并读取判分 res ...")
    results = {}
    for q in questions:
        sn = q["serialNumber"]
        r = submit_answer(q["id"], answers[sn])
        results[sn] = {"answer": answers[sn], "res": r["res"], "mark": r.get("mark")}
        time.sleep(0.1)
    wrong = {sn: v for sn, v in results.items() if v["res"] == "wrong"}
    print(f"第一轮提交：正确 {len(results) - len(wrong)} / {len(results)}，错误 {len(wrong)} 题")

    # ---- 5. 错题修正重试 ----
    for rnd in range(1, args.rounds + 1):
        if not wrong:
            break
        print(f"\n[3/3] 第 {rnd} 轮修正，待修正 {len(wrong)} 题 ...")
        # 只把错题重新交给 AI 分析
        wrong_questions = [q for q in questions if q["serialNumber"] in wrong]
        new_answers = ask_ai(wrong_questions, wrong_context=wrong)
        fixed = 0
        for q in wrong_questions:
            sn = q["serialNumber"]
            new_val = new_answers[sn]
            r = submit_answer(q["id"], new_val)
            results[sn] = {"answer": new_val, "res": r["res"], "mark": r.get("mark")}
            if r["res"] == "right":
                fixed += 1
                print(f"  #{sn} 修正为 {new_val} -> right")
            else:
                print(f"  #{sn} 仍 wrong（尝试 {new_val}）")
            time.sleep(0.1)
        wrong = {sn: v for sn, v in results.items() if v["res"] == "wrong"}
        print(f"第 {rnd} 轮修正后：仍错误 {len(wrong)} 题")
        if not wrong:
            break

    # ---- 汇总 ----
    print("\n========== 最终汇总 ==========")
    print(f"正确 {len(results) - len(wrong)} / {len(results)}")
    if wrong:
        print("Res=wrong 的题目：")
        for sn, v in sorted(wrong.items()):
            q = next(x for x in questions if x["serialNumber"] == sn)
            print(f"  #{sn} [{q['typeName']}] {q['content']} ｜ 当前答案={v['answer']}")
        print("\n提示：仍存在错题，可增大 --rounds 或人工核对上述题目后手动提交。")
    else:
        print("全部答对 ✓")

    # 保存结果供复盘
    out = {
        "questions": questions,
        "results": results,
    }
    with open("answer_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("结果已保存到 answer_results.json")


if __name__ == "__main__":
    # 关闭 https 告警（内网 http 场景）
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
    main()
