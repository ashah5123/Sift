"""Local stand-in for Jev: a small instruct model run with transformers (JEV_BACKEND=local).

Answers are read from the model's probabilities over the allowed options, not
from generated text, so every Decision carries a real probability like Jev's.

Install with: pip install -e '.[embeddings]'   (torch + transformers)
"""
import asyncio
import math
import os
import re

from sift.embeddings import strip_attachments
from sift.jev.base import Flag
from sift.models import Decision, Issue

MODEL_NAME = "Qwen/Qwen2.5-1.5B-Instruct"
MAX_BODY_CHARS = 1500  # per issue; keeps a pairwise prompt well under 2k tokens

SYSTEM = (
    "You triage GitHub issues for open-source maintainers. "
    "Reply with exactly one of the allowed answers and nothing else."
)

DEFAULT_RUBRIC = (
    "1 = security or data loss, 2 = broken for many users, 3 = normal bug, "
    "4 = minor, 5 = cosmetic or question."
)

_BLANK_LINES = re.compile(r"\n\s*\n+")

FLAG_QUESTIONS: dict[Flag, str] = {
    "ai_slop": "Does this issue read like low-effort AI-generated text rather than a real user's report?",
    "missing_repro": "Is this a bug report that is missing the steps needed to reproduce it?",
    "public_security": "Does this issue publicly disclose a security vulnerability?",
}


def issue_block(label: str, issue: Issue) -> str:
    body = _BLANK_LINES.sub("\n", strip_attachments(issue.body)).strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + " [...]"
    return f"{label} #{issue.number}\nTitle: {issue.title}\n{body}"


class LocalJev:
    """JevClient backed by a local model. Loaded on first use; one forward pass at a time."""

    def __init__(self, model_name: str | None = None, device: str | None = None):
        self.model_name = model_name or os.environ.get("JEV_LOCAL_MODEL", MODEL_NAME)
        self.device = device
        self._model = None
        self._tokenizer = None
        self._lock = asyncio.Lock()

    def _load(self):
        if self._model is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            device = self.device or ("mps" if torch.backends.mps.is_available() else "cpu")
            dtype = torch.float16 if device != "cpu" else torch.float32
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForCausalLM.from_pretrained(self.model_name, dtype=dtype).to(device).eval()
            self.device = device
        return self._model, self._tokenizer

    def _prompt_ids(self, question: str) -> list[int]:
        _, tok = self._load()
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
        text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        return tok(text, add_special_tokens=False)["input_ids"]

    def _option_probs(self, question: str, options: list[str]) -> list[float]:
        """P(option | question), normalized over `options`.

        If every option starts with a different token, one forward pass reads them
        all; otherwise each option's full token sequence is scored.
        """
        import torch

        model, tok = self._load()
        prompt = self._prompt_ids(question)
        option_ids = [tok(o, add_special_tokens=False)["input_ids"] for o in options]
        with torch.no_grad():
            firsts = [ids[0] for ids in option_ids]
            if len(set(firsts)) == len(firsts):
                logits = model(torch.tensor([prompt], device=self.device)).logits[0, -1]
                scores = torch.log_softmax(logits.float(), dim=-1)[firsts].tolist()
            else:
                scores = []
                for ids in option_ids:
                    seq = torch.tensor([prompt + ids], device=self.device)
                    logp = torch.log_softmax(model(seq).logits[0].float(), dim=-1)
                    # token at position p is predicted by the logits at p - 1
                    scores.append(sum(logp[len(prompt) + i - 1, t].item() for i, t in enumerate(ids)))
        top = max(scores)
        weights = [math.exp(s - top) for s in scores]
        total = sum(weights)
        return [w / total for w in weights]

    async def _ask(self, question: str, options: list[str]) -> list[float]:
        async with self._lock:
            return await asyncio.to_thread(self._option_probs, question, options)

    async def _yes_no(self, question: str, reason: str) -> Decision:
        p_yes, _ = await self._ask(question + "\nAnswer Yes or No.", ["Yes", "No"])
        if p_yes >= 0.5:
            return Decision("yes", p_yes, f"{reason} p(yes)={p_yes:.3f}")
        return Decision("no", 1.0 - p_yes, f"{reason} p(yes)={p_yes:.3f}")

    async def same_issue(self, new: Issue, candidate: Issue) -> Decision:
        question = (
            f"{issue_block('New issue', new)}\n\n{issue_block('Earlier issue', candidate)}\n\n"
            "Do these two issues report the same bug or request, so that the new issue "
            "should be closed as a duplicate of the earlier one? Similar topics are not "
            "enough; it must be the same underlying problem."
        )
        return await self._yes_no(question, "same_issue")

    async def check_flag(self, issue: Issue, flag: Flag) -> Decision:
        if flag not in FLAG_QUESTIONS:
            raise ValueError(f"unknown flag: {flag}")
        return await self._yes_no(f"{issue_block('Issue', issue)}\n\n{FLAG_QUESTIONS[flag]}", flag)

    async def pick_label(self, issue: Issue, labels: list[str]) -> Decision:
        options = [*labels, "none"]
        listed = "\n".join(f"- {label}" for label in options)
        question = (
            f"{issue_block('Issue', issue)}\n\nWhich one of these labels fits this issue best? "
            f"Answer with the label exactly as written, or none.\n{listed}"
        )
        probs = await self._ask(question, options)
        best = max(range(len(options)), key=probs.__getitem__)
        return Decision(options[best], probs[best], "label probabilities")

    async def score_priority(self, issue: Issue, rubric: str) -> Decision:
        question = (
            f"{issue_block('Issue', issue)}\n\nRubric:\n{rubric or DEFAULT_RUBRIC}\n\n"
            "Score this issue's priority from 1 (most urgent) to 5 (least). Answer with one digit."
        )
        options = ["1", "2", "3", "4", "5"]
        probs = await self._ask(question, options)
        best = max(range(5), key=probs.__getitem__)
        return Decision(options[best], probs[best], "priority probabilities")
