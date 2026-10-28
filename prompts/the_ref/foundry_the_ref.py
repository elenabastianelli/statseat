"""The Ref on Foundry (task AG-4): create the agent from instructions.md and test it on the questions of the examples.

    python prompts/the_ref/foundry_the_ref.py create
    python prompts/the_ref/foundry_the_ref.py eval --version V [--languages en,it,es,...] --out prompts/the_ref/foundry_test_AG4.json

`eval` sends, for each language, the 4 quiz questions of the examples of instructions.md in one call (as the server
does: one call per phase and language) and applies the server's checks (`app/agents/the_ref.py`). Run from the repo
root with `az login` and FOUNDRY_PROJECT_ENDPOINT (the .env of the repo is read); FOUNDRY_MODEL as for the other agents.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.agents import the_ref  # noqa: E402
from app.agents.settings import load_dotenv  # noqa: E402

AGENT = "the-ref"
LANGUAGES = ["en", "it", "es", "fr", "de", "nl", "zh", "ru"]


def instructions() -> str:
    return re.split(r"^---8<---$", (HERE / "instructions.md").read_text(encoding="utf-8"), flags=re.M)[1].strip()


def examples() -> list[dict]:
    """The input items of the 6 examples, in order (the JSON block after each "Input:")."""
    text = instructions()
    blocks = re.findall(r"Input:\n```json\n(.*?)```", text, re.S)
    return [json.loads(b)["items"][0] for b in blocks]


def client():
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        sys.exit("set FOUNDRY_PROJECT_ENDPOINT (.env or Foundry project > Overview > endpoint)")
    return AIProjectClient(endpoint=endpoint, credential=AzureCliCredential(process_timeout=60))


def create(args):
    from azure.ai.projects.models import PromptAgentDefinition

    model = os.environ.get("FOUNDRY_MODEL", "gpt-6-luna")
    agent = client().agents.create_version(
        agent_name=AGENT,
        description="The Ref (AG-4): rewrites the half-time quiz in the fan's language, instructions of M-5",
        definition=PromptAgentDefinition(model=model, instructions=instructions(), reasoning={"effort": "low"}),
    )
    print(f"agent {agent.name} version {agent.version} ({model}); set THE_REF_AGENT_VERSION={agent.version} in .env")


def evaluate(args):
    oa = client().get_openai_client()
    quiz = [i for i in examples() if i["game"] == "quiz"]
    runs = []
    for lang in args.languages.split(","):
        payload = {"language": lang, "items": quiz}
        start = time.monotonic()
        r = oa.responses.create(input=json.dumps(payload, ensure_ascii=False),
                                extra_body={"agent_reference": {"name": AGENT, "version": args.version,
                                                                "type": "agent_reference"}})
        latency = round(time.monotonic() - start, 1)
        try:
            got = {i.get("id"): i for i in the_ref._parse(r.output_text)["items"]}
        except Exception as exc:
            got, parse_error = {}, str(exc)
        else:
            parse_error = None
        checks = {i["id"]: the_ref.check(i, got.get(i["id"]), [], {o["id"] for o in i["options"]
                                                                   if not o["id"][0] in "BW"}) for i in quiz}
        passed = sum(v is None for v in checks.values())
        runs.append({"language": lang, "reply": r.output_text, "parse_error": parse_error, "checks": checks,
                     "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens, "latency_s": latency})
        print(f"{lang}: {passed}/{len(quiz)} ok, {r.usage.input_tokens} tok in, {r.usage.output_tokens} out, {latency} s")
        for k, v in checks.items():
            text = got.get(k, {}).get("question", "")
            print(f"   {k} {'ok' if v is None else 'KO ' + v}: {text}")
    out = {"agent": AGENT, "version": args.version, "date": time.strftime("%Y-%m-%d %H:%M"), "runs": runs}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create")
    e = sub.add_parser("eval")
    e.add_argument("--version", required=True)
    e.add_argument("--languages", default=",".join(LANGUAGES))
    e.add_argument("--out", required=True)
    args = p.parse_args()
    {"create": create, "eval": evaluate}[args.cmd](args)


if __name__ == "__main__":
    main()
