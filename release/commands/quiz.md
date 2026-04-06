Generate and take a PR comprehension quiz.

Run `python3 ~/.bryonics/current/lib/quiz.py $ARGUMENTS`.

If the command prints a quiz, render the full quiz immediately in your next response:
- include every question and every answer choice
- do not summarize it as "questions covering" or a topic list
- do not omit Q2-Q5
- do not ask follow-up questions like "which answer for Q1?" or "what's the question?"
- do not tell the user to scroll up
- do not paraphrase the quiz; relay the quiz content directly

Do NOT ask follow-up questions about individual answers. The user will submit their answers separately using /quiz-submit.

Usage:
- /quiz owner/repo#42 — quiz on a GitHub PR
- /quiz --branch — quiz on current branch diff vs main
- /quiz --branch develop — quiz on current branch diff vs develop
- /quiz --branch --new — force regenerate
- /quiz owner/repo#42 --new — force regenerate for PR
