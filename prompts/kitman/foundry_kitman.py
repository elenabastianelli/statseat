"""Kit Man on Foundry (task M-4): create the agent from the current documents and test it on the 15 questions.

    python prompts/kitman/foundry_kitman.py create [--max-results N] [--vector-store ID]
    python prompts/kitman/foundry_kitman.py eval --version V --out prompts/kitman/foundry_test_M4_<label>.json

Needs `pip install azure-ai-projects==2.9.0 azure-identity` and `az login` on an account with the Azure AI User role
on the demo project. FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL are the same names the deploy uses (infra/README.md).
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FileSearchTool, PromptAgentDefinition
from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DOCS = ROOT / "synthetic" / "SYN-001" / "kitman"
ENDPOINT = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
MODEL = os.environ.get("FOUNDRY_MODEL", "gpt-6-luna")
AGENT = "kitman"
VECTOR_STORE = "kitman-SYN-001"

# Foundry sometimes leaves its internal citation markers in the text (V-L3): fileciteturn0file2.
MARKER = re.compile(".*?", re.S)

# What a correct answer must contain (any spelling in each group), checked against synthetic/SYN-001/kitman/.
# Questions 12 and 14 must decline, so they have no keywords: they are judged by reading the answer.
EXPECTED = {
    1: [["Shimazaki"], ["Sendai"]],
    2: [["running"]],
    3: [["stomach"]],
    4: [["van Dalfsen"], ["Brightwell"]],
    5: [["4", "four"], ["Thiandoum"]],
    6: [["2018"]],
    7: [["0-0", "0–0", "goalless"]],
    8: [["Ellingham"]],
    9: [["Cranstead"], ["3.4"]],
    10: [["2004"]],
    11: [["45"]],
    13: [["Gate G"]],
    15: [["Tallowfield", "塔洛菲尔德"]],
}


def client():
    if not ENDPOINT:
        sys.exit("set FOUNDRY_PROJECT_ENDPOINT (Foundry project > Overview > endpoint)")
    return AIProjectClient(endpoint=ENDPOINT, credential=AzureCliCredential(process_timeout=60))


def upload(oa):
    files = sorted(DOCS.glob("*.md"))
    vs = oa.vector_stores.create(name=VECTOR_STORE)
    # One file at a time: polling a file batch fails on Foundry ("LRO not found", 25/10).
    for f in files:
        with open(f, "rb") as fh:
            done = oa.vector_stores.files.upload_and_poll(vector_store_id=vs.id, file=fh)
        if done.status != "completed":
            sys.exit(f"{f.name}: {done.status}")
    print(f"vector store {vs.id}: {len(files)} files")
    return vs.id


def create(args):
    project = client()
    vs_id = args.vector_store or upload(project.get_openai_client())
    instructions = (HERE / "instructions.md").read_text(encoding="utf-8").split("```text\n", 1)[1].split("```", 1)[0]
    agent = project.agents.create_version(
        agent_name=AGENT,
        description=f"Kit Man, SYN-001 documents, max_num_results={args.max_results}",
        definition=PromptAgentDefinition(
            model=MODEL,
            instructions=instructions.strip(),
            reasoning={"effort": "low"},
            tools=[FileSearchTool(vector_store_ids=[vs_id], max_num_results=args.max_results)],
        ),
    )
    print(f"agent {agent.name} version {agent.version} (max_num_results={args.max_results}, vector store {vs_id})")


def documents(response):
    """Document names cited by the answer: annotations first, else the file search results (V-L3, S-3 notes)."""
    cited, found = [], []
    for item in response.output:
        if item.type == "file_search_call":
            found += [r.filename for r in (item.results or [])]
        elif item.type == "message":
            for part in item.content:
                cited += [a.filename for a in getattr(part, "annotations", []) or [] if a.type == "file_citation"]
    return sorted(set(cited)), sorted(set(found))


def evaluate(args):
    oa = client().get_openai_client()
    vl3 = json.loads((HERE / "foundry_test_VL3.json").read_text(encoding="utf-8"))
    turns = []
    for q in vl3["runs"][0]["turns"]:
        n = q["spec_question"]
        if any(t["spec_question"] == n for t in turns):
            continue  # V-L3 asked questions 1-3 twice
        start = time.monotonic()
        r = oa.responses.create(
            input=q["question"],
            include=["file_search_call.results"],
            extra_body={"agent_reference": {"name": AGENT, "version": args.version, "type": "agent_reference"}},
        )
        latency = round(time.monotonic() - start, 1)
        raw = r.output_text
        answer = MARKER.sub("", raw).strip()
        cited, found = documents(r)
        missing = [g[0] for g in EXPECTED.get(n, []) if not any(k.lower() in answer.lower() for k in g)]
        turns.append({
            "spec_question": n,
            "question": q["question"],
            "answer": answer,
            "documents_cited": cited,
            "documents_found": found,
            "raw_citation_markers": raw != answer,
            "keywords_missing": missing,
            "input_tokens": r.usage.input_tokens,
            "output_tokens": r.usage.output_tokens,
            "latency_s": latency,
        })
        flag = "ok " if not missing else "?? "
        print(f"{flag}{n:2} {r.usage.input_tokens:6} tok {latency:4}s  {answer[:110]!r}")
    out = {"agent": AGENT, "version": args.version, "model": MODEL, "date": time.strftime("%Y-%m-%d %H:%M"), "turns": turns}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n = len(turns)
    print(f"mean input tokens {sum(t['input_tokens'] for t in turns) / n:.0f}, "
          f"mean latency {sum(t['latency_s'] for t in turns) / n:.1f} s, "
          f"keyword checks passed {sum(not t['keywords_missing'] for t in turns)}/{n}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--max-results", type=int, default=10)
    c.add_argument("--vector-store", help="reuse this vector store instead of uploading the documents again")
    e = sub.add_parser("eval")
    e.add_argument("--version", required=True)
    e.add_argument("--out", required=True)
    args = p.parse_args()
    {"create": create, "eval": evaluate}[args.cmd](args)


if __name__ == "__main__":
    main()
