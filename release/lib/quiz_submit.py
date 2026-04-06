#!/usr/bin/env python3
"""
Helper for /quiz-submit command: submit quiz answers and show results.

Usage:
    python3 quiz_submit.py <quiz_id> 1:A 2:B 3:C 4:D 5:B
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, api_request


def main():
    cfg = load_config()
    if not cfg:
        print("No Bryonics config found.")
        return

    args = sys.argv[1:]
    if not args:
        print("Usage: /quiz-submit 1:A 2:B 3:C 4:D 5:B")
        return

    # Auto-detect quiz ID from last quiz taken, or use explicit ID
    answers = {}
    quiz_id = None
    for a in args:
        if ":" in a:
            k, v = a.split(":", 1)
            # Distinguish quiz_id (has letters/underscores) from answers (number:letter)
            if k.strip().isdigit():
                answers[k.strip()] = v.strip().upper()
            elif not quiz_id:
                quiz_id = a  # explicit quiz_id like quiz_abc123
        elif not quiz_id and a.startswith("quiz_"):
            quiz_id = a

    if not quiz_id:
        # Load from last quiz session
        try:
            import json as _json
            with open(os.path.expanduser("~/.bryonics/last_quiz.json")) as f:
                quiz_id = _json.load(f).get("quiz_id")
        except (FileNotFoundError, ValueError):
            pass

    if not quiz_id:
        print("No quiz found. Run /quiz first, or provide quiz ID.")
        return

    if not answers:
        print("No valid answers found. Format: 1:A 2:B 3:C 4:D 5:B")
        return

    print("Submitting answers for quiz {}...".format(quiz_id))
    result = api_request(cfg, "POST", "/v1/quiz/{}/submit".format(quiz_id), {
        "user_id": cfg.get("user_id", "unknown"),
        "answers": answers,
    }, timeout=10.0)

    if not result:
        print("Error: Could not submit answers. Check quiz ID.")
        return

    print("")
    print("=" * 50)
    print("  SCORE: {}/{}".format(result["score"], result["total"]))
    print("=" * 50)
    print("")

    for r in result.get("results", []):
        if r["correct"]:
            print("Q{}: CORRECT ({})"
                  .format(r["question_id"], r["your_answer"]))
        else:
            print("Q{}: WRONG — you answered {}, correct was {}"
                  .format(r["question_id"], r["your_answer"], r["correct_answer"]))
            print("  Explanation: {}".format(r["explanation"]))
            for opt, expl in r.get("wrong_option_explanations", {}).items():
                if opt.upper() == r["your_answer"].upper():
                    print("  Why {} is wrong: {}".format(opt, expl))
        print("")


if __name__ == "__main__":
    main()
