Reopen a previously generated quiz (read-only — displays questions, no mutation).

Run `python3 -c "
import sys, os
sys.path.insert(0, os.path.expanduser('~/.bryonics/current/lib'))
from bryonics_client import load_config, api_request
cfg = load_config()
quiz_id = '$ARGUMENTS'.strip()
if not quiz_id:
    print('Usage: /quiz-open <quiz_id>')
else:
    quiz = api_request(cfg, 'GET', f'/v1/quiz/{quiz_id}', timeout=5.0)
    if not quiz:
        print(f'Quiz {quiz_id} not found.')
    else:
        print(f'PR #{quiz.get(\"pr_number\")}: {quiz.get(\"pr_title\",\"\")}')
        print(f'Repo: {quiz.get(\"repo\",\"\")}  |  SHA: {quiz.get(\"head_sha\",\"\")[:8]}')
        print()
        for q in quiz.get('questions', []):
            print(f'Q{q[\"id\"]}: {q[\"question\"]}')
            for letter, text in sorted(q.get('options', {}).items()):
                print(f'  {letter}: {text}')
            print()
        print(f'Quiz ID: {quiz_id}')
        print('Reply with answers: 1:B 2:A 3:C 4:D 5:B')
"`
